//! 准入名册：谁被房主放行过，以及谁正在门口等着。
//!
//! 这是内置直连的**准入闸**。P-Net-4b 阶段任何拿到 EndpointId 的人都能连上，
//! 等于把公钥当邀请凭证；本模块把它收敛成「房主亲手批准过的公钥才放行」。
//!
//! 公钥不可伪造（QUIC 的 TLS 握手证明对端持有私钥），所以这比 `X-Player-Token`
//! 那种明文串结实得多。但它认证的是**哪台机器连进来**，不是「谁在玩」——
//! 席位归属仍靠 token，ADR-007 的遗留问题不因此消失。

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tokio::sync::oneshot;

/// 门口等待的人最多站多久。房主可能不在电脑前，超时就断开让对方重试，
/// 好过攒着一堆半开的连接。
const PENDING_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(120);

#[derive(Clone, Serialize, Deserialize, Debug, PartialEq, Eq)]
pub struct ApprovedPeer {
    /// 对端 EndpointId 的字符串形式。
    pub id: String,
    /// 房主起的备注名，用来在列表里认人。
    #[serde(default)]
    pub label: String,
}

#[derive(Default, Serialize, Deserialize)]
struct RosterFile {
    #[serde(default)]
    approved: Vec<ApprovedPeer>,
}

/// 一次等待批准的接入请求（仅存在于内存）。
struct Pending {
    /// 对方**自称**的备注名，可能为空。不可信，仅供房主辨认，见 `handshake`。
    claimed_label: String,
    /// 批准/拒绝的结果由这里送回给正卡着的连接。
    decision: oneshot::Sender<bool>,
}

/// 门口等着的一位。
#[derive(Clone, Serialize, Debug, PartialEq, Eq)]
pub struct PendingPeer {
    pub id: String,
    /// 对方自称的名字（可能为空）。界面必须表述成「自称」——谁都能这么叫自己。
    pub claimed_label: String,
}

pub struct Roster {
    path: PathBuf,
    approved: Mutex<Vec<ApprovedPeer>>,
    pending: Mutex<HashMap<String, Pending>>,
}

/// 等待批准的结果。
pub enum Verdict {
    Approved,
    Rejected,
    /// 房主一直没理会。
    TimedOut,
}

impl Roster {
    /// 从磁盘载入名册。读不出来一律按空名册处理——**fail closed**：
    /// 名册损坏时谁都进不来，而不是谁都能进来。
    pub fn load(path: PathBuf) -> Self {
        let approved = std::fs::read_to_string(&path)
            .ok()
            .and_then(|raw| serde_json::from_str::<RosterFile>(&raw).ok())
            .map(|file| file.approved)
            .unwrap_or_default();
        Self {
            path,
            approved: Mutex::new(approved),
            pending: Mutex::new(HashMap::new()),
        }
    }

    pub fn is_approved(&self, id: &str) -> bool {
        self.approved.lock().unwrap().iter().any(|p| p.id == id)
    }

    pub fn approved_list(&self) -> Vec<ApprovedPeer> {
        self.approved.lock().unwrap().clone()
    }

    /// 门口正在等的人。
    pub fn pending_list(&self) -> Vec<PendingPeer> {
        self.pending
            .lock()
            .unwrap()
            .iter()
            .map(|(id, pending)| PendingPeer {
                id: id.clone(),
                claimed_label: pending.claimed_label.clone(),
            })
            .collect()
    }

    /// 登记一个陌生对端并等房主表态。调用方（连接处理）会卡在这里。
    ///
    /// `claimed_label` 是对方握手时自报的名字，只用于让房主认人。
    pub async fn wait_for_decision(&self, id: &str, claimed_label: &str) -> Verdict {
        let (tx, rx) = oneshot::channel();
        self.pending.lock().unwrap().insert(
            id.to_string(),
            Pending {
                claimed_label: claimed_label.to_string(),
                decision: tx,
            },
        );

        let verdict = match tokio::time::timeout(PENDING_TIMEOUT, rx).await {
            Ok(Ok(true)) => Verdict::Approved,
            Ok(Ok(false)) => Verdict::Rejected,
            // 发送端被丢弃（多半是房主那侧重置了状态）按拒绝处理，别放行。
            Ok(Err(_)) => Verdict::Rejected,
            Err(_) => Verdict::TimedOut,
        };
        self.pending.lock().unwrap().remove(id);
        verdict
    }

    /// 房主批准。备注名的取用顺序：房主填的 → 对方自称的 → 公钥短名。
    ///
    /// 房主填的优先，因为自称不可信；但多数时候房主懒得填，采用对方自称
    /// 已经比一串公钥好认得多。
    pub fn approve(&self, id: &str, label: Option<String>) {
        let label = label
            .filter(|l| !l.trim().is_empty())
            .or_else(|| {
                self.pending
                    .lock()
                    .unwrap()
                    .get(id)
                    .map(|p| p.claimed_label.clone())
                    .filter(|l| !l.is_empty())
            })
            .unwrap_or_else(|| short_id(id));
        {
            let mut approved = self.approved.lock().unwrap();
            if let Some(existing) = approved.iter_mut().find(|p| p.id == id) {
                existing.label = label;
            } else {
                approved.push(ApprovedPeer {
                    id: id.to_string(),
                    label,
                });
            }
        }
        self.persist();
        self.settle(id, true);
    }

    pub fn reject(&self, id: &str) {
        self.settle(id, false);
    }

    /// 吊销：既从名册里删掉，也踢掉可能正等着的同一个人。
    ///
    /// 已经建立的连接不会被本方法切断——那需要连接层配合，见 `mod.rs` 里
    /// `revoke` 的处理。
    pub fn revoke(&self, id: &str) {
        self.approved.lock().unwrap().retain(|p| p.id != id);
        self.persist();
        self.settle(id, false);
    }

    fn settle(&self, id: &str, allowed: bool) {
        if let Some(pending) = self.pending.lock().unwrap().remove(id) {
            // 接收端已消失（对方先断了）时发送失败，无所谓。
            let _ = pending.decision.send(allowed);
        }
    }

    fn persist(&self) {
        let file = RosterFile {
            approved: self.approved.lock().unwrap().clone(),
        };
        let Ok(raw) = serde_json::to_string_pretty(&file) else {
            return;
        };
        if let Some(dir) = self.path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        if let Err(e) = std::fs::write(&self.path, raw) {
            // 写不进去不该让正在进行的对局崩掉，但下次启动名册会丢，得留痕。
            log::error!("准入名册写入失败，重启后需重新批准：{e}");
        }
    }
}

/// 公钥太长，列表里显示不下，取头尾拼一个能认的短名。
fn short_id(id: &str) -> String {
    if id.len() <= 12 {
        return id.to_string();
    }
    format!("{}…{}", &id[..6], &id[id.len() - 4..])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn roster() -> (Roster, tempfile::TempDir) {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("netlink_roster.json");
        (Roster::load(path), dir)
    }

    #[test]
    fn starts_empty_and_denies_everyone() {
        let (roster, _dir) = roster();
        assert!(!roster.is_approved("stranger"));
        assert!(roster.approved_list().is_empty());
    }

    #[test]
    fn approval_persists_across_restarts() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("netlink_roster.json");

        let roster = Roster::load(path.clone());
        roster.approve("peer-a", Some("阿强".into()));
        drop(roster);

        // 重开应用后不该让朋友重新敲门。
        let reloaded = Roster::load(path);
        assert!(reloaded.is_approved("peer-a"));
        assert_eq!(reloaded.approved_list()[0].label, "阿强");
    }

    #[test]
    fn corrupt_roster_fails_closed() {
        // 名册损坏时谁都进不来，而不是谁都能进来。
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("netlink_roster.json");
        std::fs::write(&path, "{ 这不是 json").unwrap();

        let roster = Roster::load(path);
        assert!(!roster.is_approved("peer-a"));
        assert!(roster.approved_list().is_empty());
    }

    #[test]
    fn blank_label_falls_back_to_short_id() {
        let (roster, _dir) = roster();
        roster.approve("abcdefghijklmnopqrstuvwxyz", Some("   ".into()));
        assert_eq!(roster.approved_list()[0].label, "abcdef…wxyz");
    }

    #[tokio::test]
    async fn adopts_claimed_label_when_host_types_nothing() {
        // 多数时候房主懒得填备注，采用对方自称已经比一串公钥好认得多。
        let (roster, _dir) = roster();
        let roster = std::sync::Arc::new(roster);
        let waiting = {
            let roster = roster.clone();
            tokio::spawn(async move { roster.wait_for_decision("peer-a", "阿强").await })
        };
        while roster.pending_list().is_empty() {
            tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        }

        roster.approve("peer-a", None);
        let _ = waiting.await;
        assert_eq!(roster.approved_list()[0].label, "阿强");
    }

    #[tokio::test]
    async fn host_label_wins_over_claimed_one() {
        // 自称不可信，房主填了就以房主的为准。
        let (roster, _dir) = roster();
        let roster = std::sync::Arc::new(roster);
        let waiting = {
            let roster = roster.clone();
            tokio::spawn(async move { roster.wait_for_decision("peer-a", "自称管理员").await })
        };
        while roster.pending_list().is_empty() {
            tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        }

        roster.approve("peer-a", Some("老王".into()));
        let _ = waiting.await;
        assert_eq!(roster.approved_list()[0].label, "老王");
    }

    #[tokio::test]
    async fn pending_list_carries_the_claimed_label() {
        let (roster, _dir) = roster();
        let roster = std::sync::Arc::new(roster);
        let _waiting = {
            let roster = roster.clone();
            tokio::spawn(async move { roster.wait_for_decision("peer-a", "阿强").await })
        };
        while roster.pending_list().is_empty() {
            tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        }
        assert_eq!(roster.pending_list()[0].claimed_label, "阿强");
    }

    #[test]
    fn re_approving_updates_label_without_duplicating() {
        let (roster, _dir) = roster();
        roster.approve("peer-a", Some("旧名".into()));
        roster.approve("peer-a", Some("新名".into()));
        assert_eq!(roster.approved_list().len(), 1);
        assert_eq!(roster.approved_list()[0].label, "新名");
    }

    #[test]
    fn revoke_removes_from_roster() {
        let (roster, _dir) = roster();
        roster.approve("peer-a", None);
        roster.revoke("peer-a");
        assert!(!roster.is_approved("peer-a"));
    }

    #[tokio::test]
    async fn approving_releases_the_waiting_peer() {
        let (roster, _dir) = roster();
        let roster = std::sync::Arc::new(roster);

        let waiter = {
            let roster = roster.clone();
            tokio::spawn(async move { roster.wait_for_decision("peer-a", "").await })
        };
        // 等对方确实站到门口了再批准。
        while roster.pending_list().is_empty() {
            tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        }
        assert_eq!(
            roster.pending_list().iter().map(|p| p.id.as_str()).collect::<Vec<_>>(),
            vec!["peer-a"]
        );

        roster.approve("peer-a", None);
        assert!(matches!(waiter.await.unwrap(), Verdict::Approved));
        assert!(roster.pending_list().is_empty(), "批准后不该还挂在门口");
    }

    #[tokio::test]
    async fn rejecting_releases_the_waiting_peer_without_approving() {
        let (roster, _dir) = roster();
        let roster = std::sync::Arc::new(roster);

        let waiter = {
            let roster = roster.clone();
            tokio::spawn(async move { roster.wait_for_decision("peer-a", "").await })
        };
        while roster.pending_list().is_empty() {
            tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        }

        roster.reject("peer-a");
        assert!(matches!(waiter.await.unwrap(), Verdict::Rejected));
        // 拒绝不写名册。
        assert!(!roster.is_approved("peer-a"));
    }

    #[tokio::test(start_paused = true)]
    async fn unattended_request_times_out() {
        // 房主不在电脑前时，敲门的人不该无限期占着连接。
        let (roster, _dir) = roster();
        let verdict = roster.wait_for_decision("peer-a", "").await;
        assert!(matches!(verdict, Verdict::TimedOut));
        assert!(roster.pending_list().is_empty());
    }
}
