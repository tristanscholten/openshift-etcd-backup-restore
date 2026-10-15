# Requirements traceability

Status: as-built v0.1 candidate. Source: task contract and OCP 4.20 implementation contract. This document records implemented behavior, not production validation.

| ID | Requirement | Evidence | Verification |
|---|---|---|---|
| REQ-BACKUP-01 | After explicit installation confirmation, the role shall install a timer on only the nominated backup source at 00:00, 06:00, 12:00, and 18:00 UTC, with exact OCP 4.20.z and cluster identity loaded only from a root-only host EnvironmentFile. | role defaults/tasks/templates | syntax and rendered-template tests |
| REQ-BACKUP-02 | The runner shall promote only a vendor-successful snapshot/static-resource pair with metadata and SHA-256 values, validate every complete candidate before retention, then retain at most three valid managed sets. | runner script | fixture runner tests |
| REQ-DISCOVERY-01 | Read-only discovery shall invoke the validator on each managed source candidate and print metadata only for valid complete sets. | list-backups playbook | corrupted-candidate fixture and syntax check |
| REQ-RESTORE-01 | Before apply, the restore playbook shall require an explicit set ID, expected cluster ID, exact installed/selected 4.20.z match, supported topology, source/recovery hosts, exact non-recovery HA Machine map with nonempty values, and exact destructive confirmation. | restore playbook | simulated multi-host guard tests |
| REQ-RESTORE-02 | The restore playbook shall validate and fetch the selected set exactly once from the nominated source, re-hash it on recovery, fence all HA peers before one vendor restore, and defer `oc` until API recovery. | restore playbook; source evidence | simulated ordering and guard tests |
| REQ-RESTORE-03 | HA Machine recreation shall delete exactly one pre-mapped non-recovery Machine, wait for the documented stability gate, checkpoint failure, then proceed to the next Machine. | restore-ha-machine task include | simulated ordering test and syntax check |
| REQ-UNINSTALL-01 | Uninstall shall require explicit confirmation, do nothing in check mode, remove only managed schedule/helpers from the nominated source, and never remove retained backup sets. | uninstall playbook | syntax and source guard tests |
| REQ-SAFETY-01 | The repository shall document its OCP 4.20-only and non-production-validation boundary. | docs | documentation inspection |
