//! 邀请码：把「连谁 + 进哪个房间」压成一串可复制、可口述的文本。
//!
//! 形如 `coc:xu4v…7q2m:K7M9PQ2R`，冒号分段：协议前缀、房主的 EndpointId、
//! 房间码（可选——房主可能还没建房就想先把码发出去）。
//!
//! 前缀随项目改名从 `trpg` 变成 `coc`。**解析仍然接受 `trpg:`**：邀请码是发出去给
//! 别人的字符串，改名不该让已经发出去的那些当场失效；只有生成走新前缀。
//!
//! 不做加密也不做签名：EndpointId 本来就是公钥，泄露它的后果是「别人知道你的
//! 地址」，而不是「别人能进你的房间」——准入由房主批准（见 `roster`）把关。

use iroh::EndpointId;

const PREFIX: &str = "coc";
/// 改名前发出去的邀请码用的前缀，只认不发。
const LEGACY_PREFIX: &str = "trpg";

#[derive(Debug, PartialEq, Eq)]
pub struct Invite {
    pub host: EndpointId,
    pub room_code: Option<String>,
}

impl Invite {
    pub fn encode(host: &EndpointId, room_code: Option<&str>) -> String {
        match room_code {
            Some(code) if !code.is_empty() => format!("{PREFIX}:{host}:{code}"),
            _ => format!("{PREFIX}:{host}"),
        }
    }

    /// 解析用户粘贴进来的邀请码。
    ///
    /// 宽容对待人手传递的噪音：首尾空白、聊天软件加的引号、大小写不一的前缀。
    /// 但不宽容到猜——解析不出就报错，让用户去重新复制，好过连到一个错的地方。
    pub fn parse(raw: &str) -> Result<Self, String> {
        let cleaned = raw.trim().trim_matches(['"', '\'', '<', '>', '「', '」']);
        let mut parts = cleaned.split(':');

        let prefix = parts.next().unwrap_or_default();
        if !prefix.eq_ignore_ascii_case(PREFIX) && !prefix.eq_ignore_ascii_case(LEGACY_PREFIX) {
            return Err("这不像是 CoC Player 的邀请码，应当以 coc: 开头".into());
        }
        let host = parts
            .next()
            .filter(|s| !s.is_empty())
            .ok_or_else(|| "邀请码缺少房主标识，可能没复制完整".to_string())?;
        let host: EndpointId = host
            .parse()
            .map_err(|_| "邀请码里的房主标识不合法，请重新复制".to_string())?;

        let room_code = parts.next().filter(|s| !s.is_empty()).map(str::to_string);
        if parts.next().is_some() {
            return Err("邀请码格式不对，分段过多".into());
        }
        Ok(Self { host, room_code })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 一个固定的合法 EndpointId，用它的字符串形式构造各种输入。
    fn sample_id() -> EndpointId {
        iroh::SecretKey::generate().public()
    }

    #[test]
    fn round_trips_with_room_code() {
        let id = sample_id();
        let encoded = Invite::encode(&id, Some("K7M9PQ2R"));
        let parsed = Invite::parse(&encoded).unwrap();
        assert_eq!(parsed.host, id);
        assert_eq!(parsed.room_code.as_deref(), Some("K7M9PQ2R"));
    }

    #[test]
    fn round_trips_without_room_code() {
        let id = sample_id();
        let parsed = Invite::parse(&Invite::encode(&id, None)).unwrap();
        assert_eq!(parsed.host, id);
        assert_eq!(parsed.room_code, None);
    }

    #[test]
    fn treats_empty_room_code_as_absent() {
        let id = sample_id();
        assert_eq!(Invite::encode(&id, Some("")), format!("coc:{id}"));
    }

    #[test]
    fn still_accepts_codes_minted_before_the_rename() {
        // 改名前发出去的码不该当场失效——只有生成端换前缀。
        let id = sample_id();
        for raw in [format!("trpg:{id}:K7M9PQ2R"), format!("TRPG:{id}")] {
            assert_eq!(Invite::parse(&raw).unwrap().host, id, "未能解析：{raw}");
        }
    }

    #[test]
    fn tolerates_hand_delivery_noise() {
        // 从聊天软件里复制常带上这些。
        let id = sample_id();
        let encoded = Invite::encode(&id, Some("K7M9PQ2R"));
        for raw in [
            format!("  {encoded}\n"),
            format!("\"{encoded}\""),
            format!("「{encoded}」"),
            encoded.replace("coc:", "COC:"),
        ] {
            assert_eq!(Invite::parse(&raw).unwrap().host, id, "未能解析：{raw}");
        }
    }

    #[test]
    fn rejects_garbage_instead_of_guessing() {
        // 宁可报错让用户重新复制，也不要连到一个猜出来的地方。
        for raw in [
            "",
            "coc:",
            "coc",
            "trpg:",
            "https://example.com/room/abc",
            "coc:not-a-valid-key",
            "coc:xxx:yyy:zzz",
        ] {
            assert!(Invite::parse(raw).is_err(), "本该拒绝：{raw:?}");
        }
    }

    #[test]
    fn error_messages_are_actionable() {
        // 用户看到的是这句话，得能据此知道下一步做什么。
        let err = Invite::parse("https://example.com").unwrap_err();
        assert!(err.contains("coc:"), "错误信息应指出正确格式：{err}");
    }
}
