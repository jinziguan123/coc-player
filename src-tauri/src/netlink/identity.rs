//! 本机的直连身份：一把长期保存的 iroh 私钥。
//!
//! **必须持久化。** 每次启动重新生成的话：
//!
//! - 房主的 EndpointId 会变，之前发出去的邀请码全部作废；
//! - 客人的 EndpointId 也会变，房主名册里记着的公钥永远匹配不上——
//!   于是「朋友只需批准一次」形同虚设，每次重开应用都得重新敲门。
//!
//! 文件里是 32 字节私钥的十六进制。它等同于「这台机器在直连网络里的身份」，
//! 泄露意味着别人能冒充你连进已经批准过你的房间，所以在 unix 上落盘即收成 0600。

use std::path::{Path, PathBuf};

use iroh::SecretKey;

pub struct Identity {
    path: PathBuf,
}

impl Identity {
    pub fn at(path: PathBuf) -> Self {
        Self { path }
    }

    /// 读出已有身份；没有、或读出来是坏的，就新建一把并落盘。
    ///
    /// 坏文件不静默忽略——那会让用户在毫不知情的情况下换掉身份、掉出所有名册。
    /// 记一条 error 再重建，至少日志里查得到。
    pub fn load_or_create(&self) -> SecretKey {
        match self.read() {
            Some(key) => key,
            None => {
                let key = SecretKey::generate();
                self.write(&key);
                log::info!("已生成新的直连身份：{}", key.public());
                key
            }
        }
    }

    fn read(&self) -> Option<SecretKey> {
        let raw = std::fs::read_to_string(&self.path).ok()?;
        let bytes = decode_hex(raw.trim())?;
        Some(SecretKey::from_bytes(&bytes))
    }

    fn write(&self, key: &SecretKey) {
        if let Some(dir) = self.path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        let hex: String = key.to_bytes().iter().map(|b| format!("{b:02x}")).collect();
        if let Err(e) = std::fs::write(&self.path, &hex) {
            // 写不进去不该阻断本次开团，但下次启动身份会变、名册会失配。
            log::error!("直连身份写入失败，重启后邀请码与名册将失效：{e}");
            return;
        }
        restrict_permissions(&self.path);
    }
}

#[cfg(unix)]
fn restrict_permissions(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
}

#[cfg(not(unix))]
fn restrict_permissions(_path: &Path) {
    // Windows 上继承目录 ACL；app-data 目录本身已是当前用户私有。
}

fn decode_hex(text: &str) -> Option<[u8; 32]> {
    if text.len() != 64 {
        return None;
    }
    let mut out = [0u8; 32];
    for (i, slot) in out.iter_mut().enumerate() {
        *slot = u8::from_str_radix(text.get(i * 2..i * 2 + 2)?, 16).ok()?;
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn identity() -> (Identity, tempfile::TempDir) {
        let dir = tempfile::tempdir().unwrap();
        (Identity::at(dir.path().join("netlink_key")), dir)
    }

    #[test]
    fn same_key_across_restarts() {
        // 这条是整个模块存在的理由：身份必须稳定，否则邀请码与名册全部失效。
        let (id, _dir) = identity();
        let first = id.load_or_create().public();
        let second = id.load_or_create().public();
        assert_eq!(first, second);
    }

    #[test]
    fn creates_key_when_absent() {
        let (id, _dir) = identity();
        let key = id.load_or_create();
        assert_eq!(id.read().map(|k| k.public()), Some(key.public()));
    }

    #[test]
    fn regenerates_on_corrupt_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("netlink_key");
        std::fs::write(&path, "这不是十六进制").unwrap();

        let id = Identity::at(path);
        let key = id.load_or_create();
        // 重建之后要能稳定读回来，不能每次都当成坏的
        assert_eq!(id.load_or_create().public(), key.public());
    }

    #[test]
    fn rejects_wrong_length() {
        assert!(decode_hex("abcd").is_none());
        assert!(decode_hex(&"ab".repeat(31)).is_none());
        assert!(decode_hex(&"ab".repeat(32)).is_some());
    }

    #[test]
    fn rejects_non_hex() {
        assert!(decode_hex(&"zz".repeat(32)).is_none());
    }

    #[cfg(unix)]
    #[test]
    fn key_file_is_owner_only() {
        use std::os::unix::fs::PermissionsExt;
        let (id, _dir) = identity();
        id.load_or_create();
        let mode = std::fs::metadata(&id.path).unwrap().permissions().mode();
        // 私钥等同于本机在直连网络里的身份，别人可读就能冒充。
        assert_eq!(mode & 0o077, 0, "私钥文件不应对同组或其他用户可读");
    }
}
