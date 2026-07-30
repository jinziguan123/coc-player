//! 内置直连组网（P-Net-4b）：把远端客人的 HTTP/SSE 经 iroh QUIC 送到本机后端。
//!
//! ```text
//! 房主机器                                    客人机器
//!   FastAPI 127.0.0.1:8756                      前端 → 127.0.0.1:<本地端口>
//!        ▲ 反代（改写头）                              │
//!   iroh Endpoint  ◀════ QUIC，打不通走 relay ════▶  iroh Endpoint
//! ```
//!
//! **分工是刻意的**：HTTP 只在房主侧解析，客人侧是纯字节泵。这样每个请求
//! （含 keep-alive 复用连接上的后续请求）必然经过 [`rewrite::Stamp::apply`]，
//! 不存在「只有首个请求带标记」的空档。见 `rewrite` 模块的说明。
//!
//! 设计见 `docs/plans/2026-07-29-内置直连组网-design.md`。

mod handshake;
mod invite;
mod rewrite;
mod roster;

#[cfg(test)]
mod proxy_tests;

use std::net::{Ipv4Addr, SocketAddr};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use http_body_util::combinators::BoxBody;
use http_body_util::{BodyExt, Empty};
use hyper::body::{Bytes, Incoming};
use hyper::header::{HeaderValue, HOST};
use hyper::service::service_fn;
use hyper::{Request, Response, StatusCode, Uri};
use hyper_util::client::legacy::connect::HttpConnector;
use hyper_util::client::legacy::Client;
use hyper_util::rt::{TokioExecutor, TokioIo};
use hyper_util::server::conn::auto::Builder as ServerBuilder;
use iroh::endpoint::{presets, Connection};
use iroh::{Endpoint, EndpointAddr, EndpointId};
use rand::Rng;
use serde::Serialize;
use tauri::async_runtime::JoinHandle;
use tauri::{AppHandle, Emitter, Manager, State};
use tokio::net::TcpListener;

/// 隧道密钥交给后端 sidecar 的通道。必须与
/// `server/app/services/net_access.py` 的 `NETLINK_SECRET_ENV` 一致。
pub const SECRET_ENV: &str = "TRPG_NETLINK_SECRET";

/// 应用协议标识。跨版本改动它等于切断与旧客户端的连接，属于破坏性变更。
const ALPN: &[u8] = b"trpg/1";

type ProxyBody = BoxBody<Bytes, hyper::Error>;

/// 房主侧：正在接受客人接入。
struct Hosting {
    endpoint: Endpoint,
    id: String,
    /// 不带房间码的邀请码，开启时算一次。带房间码的由 `netlink_invite` 现拼。
    invite: String,
    task: JoinHandle<()>,
}

/// 客人侧：已连上某位房主，本地开了一个端口给前端用。
struct Guesting {
    endpoint: Endpoint,
    host_id: String,
    local_port: u16,
    task: JoinHandle<()>,
    /// 盯着连接何时死掉，好清理状态并通知前端，见 `netlink_connect`。
    watchdog: JoinHandle<()>,
}

/// 隧道的全部运行时状态。房主与客人两侧互不相干，可以同时存在
/// （一个人既开自己的团、又连别人的团）。
pub struct Netlink {
    /// 隧道标记密钥：进程启动时随机一次，同时经环境变量交给后端 sidecar。
    /// 两端各持一份、不落盘；局域网上的人伪造头也出示不了它。
    secret: String,
    /// 准入名册。跨重启保留，所以朋友只需被批准一次。
    roster: Arc<roster::Roster>,
    hosting: Mutex<Option<Hosting>>,
    guesting: Mutex<Option<Guesting>>,
}

impl Netlink {
    /// `data_dir` 是应用可写目录，名册落在它下面。
    pub fn new(data_dir: PathBuf) -> Self {
        // 32 个十六进制字符（128 bit）。用于相等比较而非派生密钥，够了。
        let bytes: [u8; 16] = rand::rng().random();
        let secret = bytes.iter().map(|b| format!("{b:02x}")).collect();
        Self {
            secret,
            roster: Arc::new(roster::Roster::load(data_dir.join("netlink_roster.json"))),
            hosting: Mutex::new(None),
            guesting: Mutex::new(None),
        }
    }

    pub fn secret(&self) -> &str {
        &self.secret
    }
}

#[derive(Serialize)]
pub struct NetlinkStatus {
    /// 房主侧是否已开启，以及要发给朋友的那串公钥。
    hosting: bool,
    endpoint_id: Option<String>,
    /// 直接可发出去的邀请码（含房间码时由前端另行拼接）。
    invite: Option<String>,
    /// 客人侧连着谁、前端该打哪个本地端口。
    connected_to: Option<String>,
    local_port: Option<u16>,
    /// 正在门口等房主表态的人（含各自的自称名）。
    pending: Vec<roster::PendingPeer>,
    /// 已批准的名册。
    approved: Vec<roster::ApprovedPeer>,
}

// --- 房主侧 -------------------------------------------------------------

/// 开启内置直连，返回本机的 EndpointId（要发给朋友的那串）。
///
/// 不需要打开「允许局域网加入」，也不需要重启：隧道打到的是回环，后端本来就在听。
/// 这也意味着**本开关自己就是准入闸**，关掉即不可达，见 ADR-001 与设计文档第二节。
#[tauri::command]
pub async fn netlink_start(
    app: AppHandle,
    state: State<'_, Netlink>,
    backend_port: u16,
) -> Result<String, String> {
    if let Some(running) = state.hosting.lock().unwrap().as_ref() {
        return Ok(running.id.clone());
    }

    let endpoint = Endpoint::builder(presets::N0)
        .alpns(vec![ALPN.to_vec()])
        .bind()
        .await
        .map_err(|e| format!("无法启动直连端点：{e}"))?;
    let id = endpoint.id().to_string();

    let secret = state.secret.clone();
    let roster = state.roster.clone();
    let accepting = endpoint.clone();
    let task = tauri::async_runtime::spawn(async move {
        accept_loop(accepting, backend_port, secret, roster, app).await;
    });

    state.hosting.lock().unwrap().replace(Hosting {
        invite: invite::Invite::encode(&endpoint.id(), None),
        endpoint,
        id: id.clone(),
        task,
    });
    Ok(id)
}

#[tauri::command]
pub async fn netlink_stop(state: State<'_, Netlink>) -> Result<(), String> {
    let running = state.hosting.lock().unwrap().take();
    if let Some(running) = running {
        running.task.abort();
        running.endpoint.close().await;
    }
    Ok(())
}

async fn accept_loop(
    endpoint: Endpoint,
    backend_port: u16,
    secret: String,
    roster: Arc<roster::Roster>,
    app: AppHandle,
) {
    while let Some(incoming) = endpoint.accept().await {
        let secret = secret.clone();
        let roster = roster.clone();
        let app = app.clone();
        tauri::async_runtime::spawn(async move {
            let conn = match incoming.await {
                Ok(conn) => conn,
                Err(e) => {
                    log::warn!("直连握手失败：{e}");
                    return;
                }
            };
            let peer = conn.remote_id().to_string();
            // 连接建立就留痕：排查时要能区分「压根没连上」「连上但握手卡住」
            // 「握手过了但准入没放行」这三种，否则只能盲猜。
            log::info!("直连连接已建立，等待握手：{peer}");

            // 第一条流是控制流：读对方自报的名字，把裁决写回去。之后的流才是 HTTP。
            let (mut ctrl_send, mut ctrl_recv) = match conn.accept_bi().await {
                Ok(pair) => pair,
                Err(e) => {
                    log::warn!("对端 {peer} 未开握手流：{e}");
                    return;
                }
            };
            let hello = handshake::read_hello(&mut ctrl_recv).await;
            log::info!("收到握手，对方自称「{}」：{peer}", hello.label);
            let verdict = admit(&roster, &peer, &hello.label, &app).await;
            let _ = handshake::write_verdict(&mut ctrl_send, &verdict).await;
            let _ = ctrl_send.finish();
            if verdict != handshake::Verdict::Approved {
                // 明确关掉而不是静默丢弃，客人侧才能拿到「被拒绝」而不是干等。
                conn.close(1u8.into(), b"not approved by host");
                return;
            }
            let Some(stamp) = rewrite::Stamp::new(&secret, &peer) else {
                log::error!("无法为对端 {peer} 构造隧道标记，拒绝转发");
                return;
            };
            log::info!("直连客人接入：{peer}");
            serve_connection(conn, backend_port, stamp).await;
            log::info!("直连客人断开：{peer}");
        });
    }
}

/// 事件名：有陌生人在门口等着。房主可能不在设置页，得让前端能全局提示。
pub const EVENT_PENDING: &str = "netlink://pending";
/// 事件名：门口那位已被处理（同意/拒绝/超时），前端据此收掉提示。
pub const EVENT_SETTLED: &str = "netlink://settled";
/// 事件名：客人侧与房主的连接断了（房主退出应用、关掉直连或网络中断）。
pub const EVENT_DISCONNECTED: &str = "netlink://disconnected";

#[derive(Clone, Serialize)]
struct PendingEvent {
    peer_id: String,
    claimed_label: String,
}

/// 准入判定：名册里有就直接放行，陌生人挂起等房主表态。
///
/// 挂起期间连接是保持着的——客人那侧卡在握手的裁决上，表现为「正在等待房主同意」
/// 而不是失败，这样房主点了同意，对方不必重连就能进来。
async fn admit(
    roster: &roster::Roster,
    peer: &str,
    claimed_label: &str,
    app: &AppHandle,
) -> handshake::Verdict {
    if roster.is_approved(peer) {
        return handshake::Verdict::Approved;
    }
    log::info!("陌生对端请求接入，等待房主批准：{peer}（自称「{claimed_label}」）");
    // 房主多半不在设置页，靠轮询他根本不知道有人在敲门。
    let _ = app.emit(
        EVENT_PENDING,
        PendingEvent {
            peer_id: peer.to_string(),
            claimed_label: claimed_label.to_string(),
        },
    );

    let verdict = match roster.wait_for_decision(peer, claimed_label).await {
        roster::Verdict::Approved => handshake::Verdict::Approved,
        roster::Verdict::Rejected => {
            log::info!("房主拒绝了接入请求：{peer}");
            handshake::Verdict::Rejected
        }
        roster::Verdict::TimedOut => {
            log::info!("接入请求超时无人处理：{peer}");
            handshake::Verdict::TimedOut
        }
    };
    // 无论结果如何都要通知前端，否则那条「有人敲门」的提示会一直挂着。
    let _ = app.emit(EVENT_SETTLED, peer.to_string());
    verdict
}

/// 一条 QUIC 连接上可以有多条流（前端的并发请求 + 一条长期的 SSE）。
async fn serve_connection(conn: Connection, backend_port: u16, stamp: rewrite::Stamp) {
    loop {
        let (send, recv) = match conn.accept_bi().await {
            Ok(pair) => pair,
            // 对端关闭连接是正常收尾，不是错误。
            Err(_) => return,
        };
        let stamp = stamp.clone();
        tauri::async_runtime::spawn(async move {
            proxy_stream(tokio::io::join(recv, send), backend_port, stamp).await;
        });
    }
}

/// 在一条已建立的流上跑 HTTP 反代。
///
/// 对流的类型泛型，只要求异步读写——QUIC 流之外，测试用内存管道喂它，
/// 于是「头改写是否对每个请求生效」这件事不必依赖网络就能验证。
async fn proxy_stream<I>(io: I, backend_port: u16, stamp: rewrite::Stamp)
where
    I: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin + Send + 'static,
{
    let client: Client<HttpConnector, Incoming> =
        Client::builder(TokioExecutor::new()).build_http();
    let service =
        service_fn(move |req| forward(req, backend_port, stamp.clone(), client.clone()));
    // 用 auto builder：SSE 是 HTTP/1.1 长响应，同时留出将来升级的余地。
    if let Err(e) = ServerBuilder::new(TokioExecutor::new())
        .serve_connection(TokioIo::new(io), service)
        .await
    {
        log::debug!("隧道流结束：{e}");
    }
}

/// 把一个客人请求转发给本机后端。
async fn forward(
    mut req: Request<Incoming>,
    backend_port: u16,
    stamp: rewrite::Stamp,
    client: Client<HttpConnector, Incoming>,
) -> Result<Response<ProxyBody>, hyper::Error> {
    // 安全契约：剥离客人自带的 X-Netlink-*，注入本次隧道的标记。必须在
    // 任何其它处理之前，且对每个请求都做。见 rewrite 模块。
    stamp.apply(req.headers_mut());

    let backend = format!("127.0.0.1:{backend_port}");
    let path = req
        .uri()
        .path_and_query()
        .map(|pq| pq.as_str())
        .unwrap_or("/");
    let Ok(uri) = format!("http://{backend}{path}").parse::<Uri>() else {
        return Ok(bad_gateway("请求地址无法解析"));
    };
    *req.uri_mut() = uri;
    if let Ok(host) = HeaderValue::from_str(&backend) {
        req.headers_mut().insert(HOST, host);
    }

    match client.request(req).await {
        // 响应体保持流式，不做缓冲——SSE 靠它逐条推送，缓冲等于卡死整个下行。
        Ok(res) => Ok(res.map(|body| body.boxed())),
        Err(e) => {
            log::warn!("转发到本机后端失败：{e}");
            Ok(bad_gateway("本机后端未响应"))
        }
    }
}

fn bad_gateway(reason: &str) -> Response<ProxyBody> {
    log::warn!("隧道返回 502：{reason}");
    Response::builder()
        .status(StatusCode::BAD_GATEWAY)
        .body(Empty::<Bytes>::new().map_err(|never| match never {}).boxed())
        .expect("502 响应必然可构造")
}

// --- 客人侧 -------------------------------------------------------------

/// 连上房主之后，前端需要知道的两件事。
#[derive(Serialize)]
pub struct GuestLink {
    /// 前端该打的本机端口。
    local_port: u16,
    /// 邀请码里带来的房间码，省得房主再口述一遍。
    room_code: Option<String>,
}

/// 连上房主，返回前端该打的本地端口。
///
/// 本地监听固定绑回环：这个端口是「通往房主的入口」，绝不能暴露给局域网，
/// 否则等于替房主开了一个他没同意的公网门。
///
/// `label` 是自报给房主看的备注名，可空。它**不可信**，只是让房主认人，
/// 见 `handshake` 模块。首次加入需房主手动同意，本调用可能卡上一两分钟。
#[tauri::command]
pub async fn netlink_connect(
    app: AppHandle,
    state: State<'_, Netlink>,
    invite_code: String,
    label: Option<String>,
) -> Result<GuestLink, String> {
    // 兼容两种输入：完整邀请码，以及直接粘一串裸公钥（4b 时期的用法）。
    let parsed = invite::Invite::parse(&invite_code);
    let (host_id, room_code) = match parsed {
        Ok(invite) => (invite.host, invite.room_code),
        Err(invite_err) => match invite_code.trim().parse::<EndpointId>() {
            Ok(id) => (id, None),
            // 报邀请码的错：绝大多数人粘的是邀请码，那句话更有指向性。
            Err(_) => return Err(invite_err),
        },
    };
    let endpoint_id = host_id.to_string();

    if let Some(existing) = state.guesting.lock().unwrap().as_ref() {
        if existing.host_id == endpoint_id {
            return Ok(GuestLink {
                local_port: existing.local_port,
                room_code,
            });
        }
    }
    disconnect(&state).await;

    let endpoint = Endpoint::bind(presets::N0)
        .await
        .map_err(|e| format!("无法启动直连端点：{e}"))?;
    // 只给公钥，具体地址交给 discovery 与 relay 去找。
    let conn = endpoint
        .connect(EndpointAddr::from(host_id), ALPN)
        .await
        .map_err(|e| format!("连不上房主：{e}"))?;

    // 握手：自报名字，然后等房主的裁决。首次加入时房主要手动点同意，
    // 这一步可能卡上一两分钟——前端需要在此期间显示「等待房主同意」。
    let (mut ctrl_send, mut ctrl_recv) = conn
        .open_bi()
        .await
        .map_err(|e| format!("无法与房主握手：{e}"))?;
    handshake::write_hello(&mut ctrl_send, &label.unwrap_or_default())
        .await
        .map_err(|e| format!("无法与房主握手：{e}"))?;
    let _ = ctrl_send.finish();
    match handshake::read_verdict(&mut ctrl_recv).await {
        handshake::Verdict::Approved => {}
        handshake::Verdict::Rejected => {
            endpoint.close().await;
            return Err("房主拒绝了你的加入请求".into());
        }
        handshake::Verdict::TimedOut => {
            endpoint.close().await;
            return Err("房主一直没有回应，请稍后再试".into());
        }
    }

    let listener = TcpListener::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, 0)))
        .await
        .map_err(|e| format!("无法在本机开端口：{e}"))?;
    let local_port = listener
        .local_addr()
        .map_err(|e| format!("无法读取本机端口：{e}"))?
        .port();

    let task = {
        let conn = conn.clone();
        tauri::async_runtime::spawn(async move {
            pump_loop(listener, conn).await;
        })
    };

    // 看门狗：房主那侧的进程一旦退出（或网络断掉），这条 QUIC 连接就死了。
    // 不盯着的话，客人侧会一直攥着死连接，前端每发一个请求就在上面开一次流、
    // 超时、再记一行日志——表现成「界面卡住 + 日志爆炸」，而没人告诉用户断了。
    let watchdog = {
        let app = app.clone();
        let host = endpoint_id.clone();
        tauri::async_runtime::spawn(async move {
            let reason = conn.closed().await;
            log::warn!("与房主 {host} 的直连已断开：{reason}");
            // 清掉状态，前端下次问 status 就知道没连着了。
            let netlink = app.state::<Netlink>();
            let stale = {
                let mut guard = netlink.guesting.lock().unwrap();
                // 只清理「还是这一条」的情况：用户可能已经手动重连到别处了。
                if guard.as_ref().is_some_and(|g| g.host_id == host) {
                    guard.take()
                } else {
                    None
                }
            };
            if let Some(stale) = stale {
                stale.task.abort();
                stale.endpoint.close().await;
                let _ = app.emit(EVENT_DISCONNECTED, host);
            }
        })
    };

    state.guesting.lock().unwrap().replace(Guesting {
        endpoint,
        host_id: endpoint_id,
        local_port,
        task,
        watchdog,
    });
    Ok(GuestLink {
        local_port,
        room_code,
    })
}

#[tauri::command]
pub async fn netlink_disconnect(state: State<'_, Netlink>) -> Result<(), String> {
    disconnect(&state).await;
    Ok(())
}

async fn disconnect(state: &State<'_, Netlink>) {
    let previous = state.guesting.lock().unwrap().take();
    if let Some(previous) = previous {
        previous.task.abort();
        previous.watchdog.abort();
        previous.endpoint.close().await;
    }
}

/// 本地 TCP ⇄ QUIC 流的字节泵。这一侧**不解析 HTTP**——解析和改写都在房主侧，
/// 见模块头的说明。
async fn pump_loop(listener: TcpListener, conn: Connection) {
    loop {
        let Ok((tcp, _)) = listener.accept().await else {
            return;
        };
        let conn = conn.clone();
        tauri::async_runtime::spawn(async move {
            let (mut send, mut recv) = match conn.open_bi().await {
                Ok(pair) => pair,
                Err(e) => {
                    log::warn!("无法开启隧道流：{e}");
                    return;
                }
            };
            let (mut tcp_read, mut tcp_write) = tcp.into_split();
            let upstream = async {
                let r = tokio::io::copy(&mut tcp_read, &mut send).await;
                // 让房主侧读到请求结束，否则它会一直等 body。
                let _ = send.finish();
                r
            };
            let downstream = tokio::io::copy(&mut recv, &mut tcp_write);
            if let Err(e) = tokio::try_join!(upstream, downstream) {
                log::debug!("隧道流结束：{e}");
            }
        });
    }
}

// --- 状态查询 -----------------------------------------------------------

#[tauri::command]
pub fn netlink_status(state: State<'_, Netlink>) -> NetlinkStatus {
    let hosting = state.hosting.lock().unwrap();
    let guesting = state.guesting.lock().unwrap();
    NetlinkStatus {
        hosting: hosting.is_some(),
        endpoint_id: hosting.as_ref().map(|h| h.id.clone()),
        invite: hosting.as_ref().map(|h| h.invite.clone()),
        connected_to: guesting.as_ref().map(|g| g.host_id.clone()),
        local_port: guesting.as_ref().map(|g| g.local_port),
        pending: state.roster.pending_list(),
        approved: state.roster.approved_list(),
    }
}

// --- 准入名册 -----------------------------------------------------------

/// 批准一个正在门口等的对端，并记进名册（下次直接放行）。
#[tauri::command]
pub fn netlink_approve(
    state: State<'_, Netlink>,
    peer_id: String,
    label: Option<String>,
) -> Result<(), String> {
    state.roster.approve(&peer_id, label);
    Ok(())
}

#[tauri::command]
pub fn netlink_reject(state: State<'_, Netlink>, peer_id: String) -> Result<(), String> {
    state.roster.reject(&peer_id);
    Ok(())
}

/// 吊销。已建立的连接**不会**被立即切断，见下方说明。
#[tauri::command]
pub fn netlink_revoke(state: State<'_, Netlink>, peer_id: String) -> Result<(), String> {
    state.roster.revoke(&peer_id);
    // 名册只在建立连接时查一次，所以吊销对当前还连着的人不生效——他要断线重连
    // 才会被挡住。真正的「踢人下线」需要连接层保留句柄并主动 close，留给后续。
    log::info!("已吊销 {peer_id}；若对方仍连着，重连后才会被挡住");
    Ok(())
}

/// 生成邀请码。房间码可选——房主可能还没建房就想先把码发出去。
#[tauri::command]
pub fn netlink_invite(
    state: State<'_, Netlink>,
    room_code: Option<String>,
) -> Result<String, String> {
    let hosting = state.hosting.lock().unwrap();
    let hosting = hosting.as_ref().ok_or("尚未开启内置直连")?;
    let host: EndpointId = hosting
        .id
        .parse()
        .map_err(|_| "本机端点标识异常".to_string())?;
    Ok(invite::Invite::encode(&host, room_code.as_deref()))
}
