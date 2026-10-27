# Windows 安全存储

通过 Windows Credential Manager 保存、读取和删除 API Key，不维护明文配置文件。

接口：save_key(key)、load_key()、delete_key()。正式凭据目标为 RepoLens/DeepSeek，仅当前 Windows 用户可读；读取后释放系统缓冲。

未勾选保存时只在服务内存中使用。重新设置临时 Key 会删除此前保存的正式凭据，避免下次启动使用旧值。“清除”同时移除内存值与正式凭据。非 Windows 不支持持久保存。

验证：CredentialTests 对独立随机 RepoLens/Test 目标执行虚拟凭据往返，并在 finally 清理，不覆盖正式目标。运行方式见 [服务说明](../../../../README.md)。当前仅支持一个账户。
