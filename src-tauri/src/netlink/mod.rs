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

mod rewrite;

#[cfg(test)]
mod proxy_tests;

use std::net::{Ipv4Addr, SocketAddr};
use std::sync::Mutex;

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
use tauri::State;
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
    task: JoinHandle<()>,
}

/// 客人侧：已连上某位房主，本地开了一个端口给前端用。
struct Guesting {
    endpoint: Endpoint,
    host_id: String,
    local_port: u16,
    task: JoinHandle<()>,
}

/// 隧道的全部运行时状态。房主与客人两侧互不相干，可以同时存在
/// （一个人既开自己的团、又连别人的团）。
pub struct Netlink {
    /// 隧道标记密钥：进程启动时随机一次，同时经环境变量交给后端 sidecar。
    /// 两端各持一份、不落盘；局域网上的人伪造头也出示不了它。
    secret: String,
    hosting: Mutex<Option<Hosting>>,
    guesting: Mutex<Option<Guesting>>,
}

impl Netlink {
    pub fn new() -> Self {
        // 32 个十六进制字符（128 bit）。用于相等比较而非派生密钥，够了。
        let bytes: [u8; 16] = rand::rng().random();
        let secret = bytes.iter().map(|b| format!("{b:02x}")).collect();
        Self {
            secret,
            hosting: Mutex::new(None),
            guesting: Mutex::new(None),
        }
    }

    pub fn secret(&self) -> &str {
        &self.secret
    }
}

impl Default for Netlink {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Serialize)]
pub struct NetlinkStatus {
    /// 房主侧是否已开启，以及要发给朋友的那串公钥。
    hosting: bool,
    endpoint_id: Option<String>,
    /// 客人侧连着谁、前端该打哪个本地端口。
    connected_to: Option<String>,
    local_port: Option<u16>,
}

// --- 房主侧 -------------------------------------------------------------

/// 开启内置直连，返回本机的 EndpointId（要发给朋友的那串）。
///
/// 不需要打开「允许局域网加入」，也不需要重启：隧道打到的是回环，后端本来就在听。
/// 这也意味着**本开关自己就是准入闸**，关掉即不可达，见 ADR-001 与设计文档第二节。
#[tauri::command]
pub async fn netlink_start(
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
    let accepting = endpoint.clone();
    let task = tauri::async_runtime::spawn(async move {
        accept_loop(accepting, backend_port, secret).await;
    });

    state.hosting.lock().unwrap().replace(Hosting {
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

async fn accept_loop(endpoint: Endpoint, backend_port: u16, secret: String) {
    while let Some(incoming) = endpoint.accept().await {
        let secret = secret.clone();
        tauri::async_runtime::spawn(async move {
            let conn = match incoming.await {
                Ok(conn) => conn,
                Err(e) => {
                    log::warn!("直连握手失败：{e}");
                    return;
                }
            };
            // P-Net-4c 会在这里插入白名单与房主批准；当前阶段任何拿到
            // EndpointId 的人都能连上，等同于把公钥当作邀请凭证。
            let peer = conn.remote_id().to_string();
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

/// 连上房主，返回前端该打的本地端口。
///
/// 本地监听固定绑回环：这个端口是「通往房主的入口」，绝不能暴露给局域网，
/// 否则等于替房主开了一个他没同意的公网门。
#[tauri::command]
pub async fn netlink_connect(
    state: State<'_, Netlink>,
    endpoint_id: String,
) -> Result<u16, String> {
    if let Some(existing) = state.guesting.lock().unwrap().as_ref() {
        if existing.host_id == endpoint_id {
            return Ok(existing.local_port);
        }
    }
    disconnect(&state).await;

    let host_id: EndpointId = endpoint_id
        .parse()
        .map_err(|_| "这串房主标识格式不对，请检查是否复制完整".to_string())?;

    let endpoint = Endpoint::bind(presets::N0)
        .await
        .map_err(|e| format!("无法启动直连端点：{e}"))?;
    // 只给公钥，具体地址交给 discovery 与 relay 去找。
    let conn = endpoint
        .connect(EndpointAddr::from(host_id), ALPN)
        .await
        .map_err(|e| format!("连不上房主：{e}"))?;

    let listener = TcpListener::bind(SocketAddr::from((Ipv4Addr::LOCALHOST, 0)))
        .await
        .map_err(|e| format!("无法在本机开端口：{e}"))?;
    let local_port = listener
        .local_addr()
        .map_err(|e| format!("无法读取本机端口：{e}"))?
        .port();

    let task = tauri::async_runtime::spawn(async move {
        pump_loop(listener, conn).await;
    });

    state.guesting.lock().unwrap().replace(Guesting {
        endpoint,
        host_id: endpoint_id,
        local_port,
        task,
    });
    Ok(local_port)
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
        connected_to: guesting.as_ref().map(|g| g.host_id.clone()),
        local_port: guesting.as_ref().map(|g| g.local_port),
    }
}
