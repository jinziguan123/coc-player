//! 隧道请求头改写——**本模块是内置直连的安全契约所在**。
//!
//! 后端把「来自回环」当作「房主本人」，而隧道会把远端客人的请求以 `127.0.0.1`
//! 反代进本机后端。后端那一侧靠 `X-Netlink-Secret` 区分二者（见
//! `server/app/services/net_access.py` 的 `peer_kind`），但它**无法自行验证**
//! 这个头是不是客人伪造的——回环请求里，房主前端和隧道客人长得一模一样。
//!
//! 所以责任在这里：转发前必须**无条件剥离**客户端自带的所有 `X-Netlink-*`，
//! 再注入本次隧道的标记。漏掉剥离，客人只要自己发一个假头就能……不，更糟：
//! 客人只要**什么都不发**，后端就会把他判成 `local`，于是拿到房主的明文
//! API key、素材库删除权和限速豁免。剥离与注入必须一起发生、对每个请求发生。
//!
//! 设计见 `docs/plans/2026-07-29-内置直连组网-design.md` 第三节。

use hyper::header::{HeaderMap, HeaderName, HeaderValue};

pub const SECRET_HEADER: &str = "x-netlink-secret";
pub const PEER_HEADER: &str = "x-netlink-peer";

/// 剥离范围。用前缀而不是精确匹配：将来加 `X-Netlink-*` 的新头时，
/// 不会因为忘了更新这里而留下一个可伪造的字段。
const NETLINK_PREFIX: &str = "x-netlink-";

/// 一条隧道连接的标记。密钥每次启动随机、对端公钥每条连接固定，
/// 都在建立连接时校验成 `HeaderValue`，免得每个请求重做一遍还要处理失败。
#[derive(Clone)]
pub struct Stamp {
    secret: HeaderValue,
    peer: HeaderValue,
}

impl Stamp {
    /// 值必须是合法的 HTTP 头值（可见 ASCII）。密钥是我们生成的 hex、
    /// 对端公钥是 iroh 的 z-base-32，都满足；不满足就没有隧道，不能降级放行。
    pub fn new(secret: &str, peer: &str) -> Option<Self> {
        Some(Self {
            secret: HeaderValue::from_str(secret).ok()?,
            peer: HeaderValue::from_str(peer).ok()?,
        })
    }

    /// 剥离 + 注入。对**每个**经隧道转发的请求调用，不是每条连接一次——
    /// HTTP keep-alive 下一条连接会承载多个请求，漏掉后续请求就等于漏掉标记。
    pub fn apply(&self, headers: &mut HeaderMap) {
        strip_client_marks(headers);
        headers.insert(SECRET_HEADER, self.secret.clone());
        headers.insert(PEER_HEADER, self.peer.clone());
    }
}

fn strip_client_marks(headers: &mut HeaderMap) {
    // HeaderMap 的键已规范化为小写，前缀比较是安全的。
    let forged: Vec<HeaderName> = headers
        .keys()
        .filter(|name| name.as_str().starts_with(NETLINK_PREFIX))
        .cloned()
        .collect();
    for name in forged {
        headers.remove(&name);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stamp() -> Stamp {
        Stamp::new("real-secret", "peer-abc").unwrap()
    }

    fn header(map: &HeaderMap, name: &str) -> Option<String> {
        map.get(name)
            .map(|v| v.to_str().unwrap_or_default().to_string())
    }

    #[test]
    fn injects_mark_on_clean_request() {
        let mut headers = HeaderMap::new();
        stamp().apply(&mut headers);
        assert_eq!(header(&headers, SECRET_HEADER).as_deref(), Some("real-secret"));
        assert_eq!(header(&headers, PEER_HEADER).as_deref(), Some("peer-abc"));
    }

    #[test]
    fn overwrites_forged_secret() {
        // 客人伪造一个密钥想冒充别的来源。
        let mut headers = HeaderMap::new();
        headers.insert(SECRET_HEADER, HeaderValue::from_static("forged"));
        stamp().apply(&mut headers);
        assert_eq!(header(&headers, SECRET_HEADER).as_deref(), Some("real-secret"));
    }

    #[test]
    fn overwrites_forged_peer_id() {
        // 冒充另一个玩家的公钥（会污染按公钥分桶的限速）。
        let mut headers = HeaderMap::new();
        headers.insert(PEER_HEADER, HeaderValue::from_static("someone-else"));
        stamp().apply(&mut headers);
        assert_eq!(header(&headers, PEER_HEADER).as_deref(), Some("peer-abc"));
    }

    #[test]
    fn strips_unknown_netlink_headers() {
        // 前缀内的任何字段都不能由客人带进来，哪怕后端当前还不认识它。
        let mut headers = HeaderMap::new();
        headers.insert("x-netlink-future-flag", HeaderValue::from_static("1"));
        stamp().apply(&mut headers);
        assert!(headers.get("x-netlink-future-flag").is_none());
    }

    #[test]
    fn strips_duplicate_values() {
        // append 而非 insert 时同名头会有多个值，remove 必须清干净。
        let mut headers = HeaderMap::new();
        headers.append(SECRET_HEADER, HeaderValue::from_static("forged-1"));
        headers.append(SECRET_HEADER, HeaderValue::from_static("forged-2"));
        stamp().apply(&mut headers);
        let all: Vec<_> = headers.get_all(SECRET_HEADER).iter().collect();
        assert_eq!(all.len(), 1);
        assert_eq!(all[0], "real-secret");
    }

    #[test]
    fn is_case_insensitive_about_forged_headers() {
        // HTTP 头大小写不敏感，客人用 X-NETLINK-SECRET 同样要被剥掉。
        let mut headers = HeaderMap::new();
        headers.insert(
            HeaderName::from_static("x-netlink-secret"),
            HeaderValue::from_static("forged"),
        );
        stamp().apply(&mut headers);
        assert_eq!(header(&headers, "X-Netlink-Secret").as_deref(), Some("real-secret"));
    }

    #[test]
    fn leaves_unrelated_headers_alone() {
        let mut headers = HeaderMap::new();
        headers.insert("x-player-token", HeaderValue::from_static("abc"));
        headers.insert("accept", HeaderValue::from_static("text/event-stream"));
        stamp().apply(&mut headers);
        assert_eq!(header(&headers, "x-player-token").as_deref(), Some("abc"));
        assert_eq!(header(&headers, "accept").as_deref(), Some("text/event-stream"));
    }

    #[test]
    fn rejects_values_that_would_inject_headers() {
        // 造不出合法标记时必须失败，不能降级成「不打标记照样转发」。
        // 注意 HeaderValue 允许 obs-text（0x80-0xFF），所以中文这类非 ASCII
        // 反而是合法值；真正要挡的是换行——它能把一个头值劈成两个头。
        assert!(Stamp::new("secret\r\nx-netlink-peer: someone-else", "peer").is_none());
        assert!(Stamp::new("secret", "peer\n").is_none());
        assert!(Stamp::new("secret\0", "peer").is_none());
    }
}
