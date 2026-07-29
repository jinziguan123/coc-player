//! 反代层的端到端验证：请求经隧道流打到一个假后端，检查它**实际收到**了什么。
//!
//! 与 `rewrite` 模块的纯函数单测互补——那里验证改写函数本身，这里验证它确实被
//! 挂在了转发路径上、且对每个请求生效。用内存管道代替 QUIC 流，所以不依赖网络。
//! iroh 的连接建立与字节泵不在此覆盖，只能靠真机跨网验证。

use std::sync::{Arc, Mutex};

use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;

use super::{proxy_stream, rewrite::Stamp};

/// 假后端收到的一个请求的全部头（小写名 → 值）。
type Headers = Vec<(String, String)>;

/// 起一个只会照本宣科回话的后端，把收到的请求头记下来供断言。
/// `response` 是它对每个请求的完整 HTTP 响应文本。
async fn fake_backend(response: &'static str) -> (u16, Arc<Mutex<Vec<Headers>>>) {
    let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let seen: Arc<Mutex<Vec<Headers>>> = Arc::new(Mutex::new(Vec::new()));

    let recorder = seen.clone();
    tokio::spawn(async move {
        while let Ok((stream, _)) = listener.accept().await {
            let recorder = recorder.clone();
            tokio::spawn(async move {
                let (read, mut write) = stream.into_split();
                let mut lines = BufReader::new(read).lines();
                let mut headers: Headers = Vec::new();
                while let Ok(Some(line)) = lines.next_line().await {
                    if line.is_empty() {
                        // 一个请求的头读完了：记录并应答，然后继续等下一个
                        // （keep-alive 下同一条连接会有多个请求）。
                        recorder.lock().unwrap().push(std::mem::take(&mut headers));
                        if write.write_all(response.as_bytes()).await.is_err() {
                            return;
                        }
                        continue;
                    }
                    if let Some((name, value)) = line.split_once(':') {
                        headers.push((name.to_ascii_lowercase(), value.trim().to_string()));
                    }
                }
            });
        }
    });
    (port, seen)
}

/// 把一段原始 HTTP 请求文本喂进隧道流，返回客人侧收到的原始响应文本。
///
/// 请求里必须让最后一个请求带 `connection: close`，否则 HTTP/1.1 的 keep-alive
/// 会让反代一直握着连接，这里就读不到 EOF。超时是为了让「挂住」表现为一条
/// 失败断言而不是一个永远不返回的测试。
async fn through_tunnel(backend_port: u16, raw_request: &str) -> String {
    let (guest_side, host_side) = tokio::io::duplex(64 * 1024);
    let stamp = Stamp::new("real-secret", "peer-abc").unwrap();
    tokio::spawn(proxy_stream(host_side, backend_port, stamp));

    let (mut read, mut write) = tokio::io::split(guest_side);
    write.write_all(raw_request.as_bytes()).await.unwrap();
    write.flush().await.unwrap();

    let mut response = String::new();
    tokio::time::timeout(
        std::time::Duration::from_secs(5),
        read.read_to_string(&mut response),
    )
    .await
    .expect("反代未在 5 秒内收尾：响应没结束，或连接没按 connection: close 关闭")
    .unwrap();
    response
}

fn value<'a>(headers: &'a Headers, name: &str) -> Option<&'a str> {
    headers
        .iter()
        .find(|(k, _)| k == name)
        .map(|(_, v)| v.as_str())
}

const OK: &str = "HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\nok";

#[tokio::test]
async fn backend_sees_injected_mark() {
    let (port, seen) = fake_backend(OK).await;
    let response = through_tunnel(port, "GET /api/health HTTP/1.1\r\nhost: x\r\nconnection: close\r\n\r\n").await;

    assert!(response.starts_with("HTTP/1.1 200"), "响应：{response}");
    let seen = seen.lock().unwrap();
    assert_eq!(seen.len(), 1);
    assert_eq!(value(&seen[0], "x-netlink-secret"), Some("real-secret"));
    assert_eq!(value(&seen[0], "x-netlink-peer"), Some("peer-abc"));
}

#[tokio::test]
async fn backend_never_sees_forged_mark() {
    // 客人自己伪造标记想冒充房主本机（不带标记）或别的玩家。
    let (port, seen) = fake_backend(OK).await;
    through_tunnel(
        port,
        "GET /api/settings/ai/profiles HTTP/1.1\r\nhost: x\r\nconnection: close\r\n\
         x-netlink-secret: forged\r\nx-netlink-peer: someone-else\r\n\r\n",
    )
    .await;

    let seen = seen.lock().unwrap();
    assert_eq!(value(&seen[0], "x-netlink-secret"), Some("real-secret"));
    assert_eq!(value(&seen[0], "x-netlink-peer"), Some("peer-abc"));
}

#[tokio::test]
async fn every_request_on_a_reused_connection_is_stamped() {
    // 最要紧的一条：keep-alive 下一条流承载多个请求。若只改写首个请求，
    // 后续请求会被后端判成 local——客人凭「什么都不发」升权成房主。
    let (port, seen) = fake_backend(OK).await;
    through_tunnel(
        port,
        "GET /api/health HTTP/1.1\r\nhost: x\r\n\r\n\
         GET /api/settings/ai/profiles HTTP/1.1\r\nhost: x\r\nconnection: close\r\n\r\n",
    )
    .await;

    let seen = seen.lock().unwrap();
    assert_eq!(seen.len(), 2, "两个请求都应到达后端");
    for (i, headers) in seen.iter().enumerate() {
        assert_eq!(
            value(headers, "x-netlink-secret"),
            Some("real-secret"),
            "第 {} 个请求缺少隧道标记",
            i + 1
        );
    }
}

#[tokio::test]
async fn player_token_survives_the_tunnel() {
    // 席位归属靠它，被剥掉的话客人一进门就掉座位。
    let (port, seen) = fake_backend(OK).await;
    through_tunnel(
        port,
        "GET /api/sessions HTTP/1.1\r\nhost: x\r\nx-player-token: seat-42\r\nconnection: close\r\n\r\n",
    )
    .await;

    assert_eq!(value(&seen.lock().unwrap()[0], "x-player-token"), Some("seat-42"));
}

#[tokio::test]
async fn host_header_points_at_the_local_backend() {
    let (port, seen) = fake_backend(OK).await;
    through_tunnel(port, "GET /api/health HTTP/1.1\r\nhost: 127.0.0.1:9999\r\nconnection: close\r\n\r\n").await;

    assert_eq!(
        value(&seen.lock().unwrap()[0], "host"),
        Some(format!("127.0.0.1:{port}").as_str())
    );
}

#[tokio::test]
async fn unreachable_backend_yields_502() {
    // 后端还没起来时客人该拿到一个明确的失败，而不是连接被静默挂死。
    let dead_port = {
        let probe = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        probe.local_addr().unwrap().port() // listener 随即释放，端口无人监听
    };
    let response = through_tunnel(dead_port, "GET /api/health HTTP/1.1\r\nhost: x\r\nconnection: close\r\n\r\n").await;
    assert!(response.starts_with("HTTP/1.1 502"), "响应：{response}");
}

#[tokio::test]
async fn sse_chunks_arrive_before_the_stream_ends() {
    // SSE 是整个下行的载体。若反代缓冲响应体，事件要等连接关闭才到，
    // 表现就是「联机后画面不动」。这里断言首条事件在后端仍握着连接时就已抵达。
    let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let (release, hold) = tokio::sync::oneshot::channel::<()>();

    tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let (_read, mut write) = stream.into_split();
        write
            .write_all(
                b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n\
                  transfer-encoding: chunked\r\n\r\n",
            )
            .await
            .unwrap();
        // 一条完整的 chunked 分块，然后**不关闭**连接。
        write.write_all(b"10\r\ndata: first\n\n\r\n").await.unwrap();
        write.flush().await.unwrap();
        let _ = hold.await; // 等测试确认收到后再收尾
        let _ = write.write_all(b"0\r\n\r\n").await;
    });

    let (guest_side, host_side) = tokio::io::duplex(64 * 1024);
    let stamp = Stamp::new("real-secret", "peer-abc").unwrap();
    tokio::spawn(proxy_stream(host_side, port, stamp));

    let (read, mut write) = tokio::io::split(guest_side);
    write
        .write_all(b"GET /api/sessions/x/live HTTP/1.1\r\nhost: x\r\n\r\n")
        .await
        .unwrap();
    write.flush().await.unwrap();

    // 只读到首条事件为止；读得到就证明它没被攒在缓冲里。
    // 整段加超时：缓冲的表现正是「永远读不到」，不设限就会挂死而非失败。
    let seek_event = async {
        let mut lines = BufReader::new(read).lines();
        let mut got_event = false;
        while let Ok(Some(line)) = lines.next_line().await {
            if line.contains("data: first") {
                got_event = true;
                break;
            }
        }
        got_event
    };

    let got_event = tokio::time::timeout(std::time::Duration::from_secs(5), seek_event)
        .await
        .unwrap_or(false);
    assert!(got_event, "首条 SSE 事件应在后端仍持有连接时就抵达客人侧");
    let _ = release.send(());
}
