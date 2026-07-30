//! 接入握手：客人自报备注名，房主回批准结果。
//!
//! 客人连上后开的**第一条**双向流是控制流，只用于这一次握手；之后的流才是
//! HTTP 隧道。这样房主在决定放不放人之前就知道对方自称是谁，客人也能明确
//! 得知自己是被同意、被拒绝，还是房主压根没理。
//!
//! **自报的名字不可信。** 谁都能把自己叫做「阿强」，它只是给房主辨认用的提示，
//! 真正的身份是 EndpointId（公钥，QUIC 握手已证明对端持有私钥）。界面上必须
//! 表述成「对方自称」，房主批准时可以改写成自己认得的备注。
//!
//! 线格式是一行 JSON + `\n`，够简单也够扩展：将来加字段不必改协议版本。

use serde::{Deserialize, Serialize};
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader};

/// 自报名的长度上限。防的是对端塞一个几 MB 的字符串把房主内存吃掉，
/// 同时也是界面上显示得下的长度。
const MAX_LABEL_CHARS: usize = 24;

/// 一行的字节上限，配合上面的字符数留足 UTF-8 与 JSON 转义的余量。
const MAX_LINE_BYTES: u64 = 4096;

#[derive(Serialize, Deserialize, Debug, Default, PartialEq, Eq)]
pub struct Hello {
    /// 客人自称的备注名，可为空（房主会看到公钥短名）。
    #[serde(default)]
    pub label: String,
}

#[derive(Serialize, Deserialize, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case", tag = "decision")]
pub enum Verdict {
    Approved,
    Rejected,
    /// 房主一直没理会，连接已超时。
    TimedOut,
}

/// 截断并清理自报名：去掉首尾空白与控制字符，限长。
///
/// 控制字符必须清掉——它们会在界面上造成看不见的差异，正好用来伪装成别人
/// （「阿强」与「阿强\u{200e}」肉眼无法区分）。
pub fn sanitize_label(raw: &str) -> String {
    let cleaned: String = raw
        .trim()
        .chars()
        .filter(|c| !c.is_control())
        .take(MAX_LABEL_CHARS)
        .collect();
    cleaned.trim().to_string()
}

pub async fn write_hello<W: AsyncWrite + Unpin>(
    stream: &mut W,
    label: &str,
) -> std::io::Result<()> {
    let hello = Hello {
        label: sanitize_label(label),
    };
    write_line(stream, &serde_json::to_string(&hello).unwrap_or_default()).await
}

/// 读客人的自报。读不出来不算致命——按「没自报」处理，房主看公钥短名即可，
/// 不能因为一行 JSON 坏掉就把人挡在门外（那会变成一个难查的连接失败）。
pub async fn read_hello<R: AsyncRead + Unpin>(stream: &mut R) -> Hello {
    match read_line(stream).await {
        Some(line) => {
            let mut hello: Hello = serde_json::from_str(&line).unwrap_or_default();
            hello.label = sanitize_label(&hello.label);
            hello
        }
        None => Hello::default(),
    }
}

pub async fn write_verdict<W: AsyncWrite + Unpin>(
    stream: &mut W,
    verdict: &Verdict,
) -> std::io::Result<()> {
    write_line(stream, &serde_json::to_string(verdict).unwrap_or_default()).await
}

/// 读房主的裁决。读不到（连接被掐、格式坏了）一律按拒绝处理——**fail closed**：
/// 拿不到明确的「同意」就不该往下走。
pub async fn read_verdict<R: AsyncRead + Unpin>(stream: &mut R) -> Verdict {
    match read_line(stream).await {
        Some(line) => serde_json::from_str(&line).unwrap_or(Verdict::Rejected),
        None => Verdict::Rejected,
    }
}

async fn write_line<W: AsyncWrite + Unpin>(stream: &mut W, line: &str) -> std::io::Result<()> {
    stream.write_all(line.as_bytes()).await?;
    stream.write_all(b"\n").await?;
    stream.flush().await
}

async fn read_line<R: AsyncRead + Unpin>(stream: &mut R) -> Option<String> {
    let mut line = String::new();
    // take() 限住字节数：对端不发换行符时不至于一直读到内存耗尽。
    let mut limited = BufReader::new(stream.take(MAX_LINE_BYTES));
    match limited.read_line(&mut line).await {
        Ok(0) | Err(_) => None,
        Ok(_) => Some(line),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_ordinary_labels() {
        assert_eq!(sanitize_label("阿强"), "阿强");
        assert_eq!(sanitize_label("  阿强  "), "阿强");
        assert_eq!(sanitize_label(""), "");
    }

    #[test]
    fn strips_control_chars_used_for_impersonation() {
        // 不可见字符能让两个「阿强」在界面上无法区分。
        assert_eq!(sanitize_label("阿强\u{0}"), "阿强");
        assert_eq!(sanitize_label("阿\n强"), "阿强");
        assert_eq!(sanitize_label("阿\t强"), "阿强");
    }

    #[test]
    fn caps_absurd_labels() {
        let long = "强".repeat(200);
        assert_eq!(sanitize_label(&long).chars().count(), MAX_LABEL_CHARS);
    }

    #[tokio::test]
    async fn hello_round_trips() {
        let (mut a, mut b) = tokio::io::duplex(1024);
        write_hello(&mut a, "阿强").await.unwrap();
        assert_eq!(read_hello(&mut b).await.label, "阿强");
    }

    #[tokio::test]
    async fn empty_label_round_trips() {
        let (mut a, mut b) = tokio::io::duplex(1024);
        write_hello(&mut a, "   ").await.unwrap();
        assert_eq!(read_hello(&mut b).await.label, "");
    }

    #[tokio::test]
    async fn garbage_hello_is_treated_as_anonymous() {
        // 坏掉的自报不该变成连接失败——房主看公钥就是了。
        let (mut a, mut b) = tokio::io::duplex(1024);
        a.write_all(b"{ not json\n").await.unwrap();
        assert_eq!(read_hello(&mut b).await.label, "");
    }

    #[tokio::test]
    async fn verdict_round_trips() {
        for expected in [Verdict::Approved, Verdict::Rejected, Verdict::TimedOut] {
            let (mut a, mut b) = tokio::io::duplex(1024);
            write_verdict(&mut a, &expected).await.unwrap();
            assert_eq!(read_verdict(&mut b).await, expected);
        }
    }

    #[tokio::test]
    async fn unreadable_verdict_fails_closed() {
        // 拿不到明确的同意就不能放行。
        let (mut a, mut b) = tokio::io::duplex(1024);
        a.write_all(b"{ garbage\n").await.unwrap();
        assert_eq!(read_verdict(&mut b).await, Verdict::Rejected);

        let (a, mut b) = tokio::io::duplex(1024);
        drop(a); // 连接直接断掉
        assert_eq!(read_verdict(&mut b).await, Verdict::Rejected);
    }
}
