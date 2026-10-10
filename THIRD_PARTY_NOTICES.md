# 依赖来源

vendor/aoguai-crypto/draft_crypto.py 来源 aoguai/pyJianYingDraft，Apache-2.0，原LICENSE随目录保留。该独立模块通过本机剪映videoeditor.dll在子进程解密，不分发DLL。分析只对用户授权的工程副本执行。

本 Skill 自有说明与执行器采用 MIT；见 LICENSE。

vendor/jianying-editor 的 scripts/ 和 data/ 来自 luoluoluo22/jianying-editor-skill，固定提交 `32c56928ded4f9e2c2b80e099dc7abb793d2c30b`，按原 MIT LICENSE 保留。其内置 pyJianYingDraft 遵守 Apache-2.0，许可证保留在 vendor/jianying-editor/scripts/vendor/pyJianYingDraft/LICENSE。本版未修改上游源码。

vendor/jianying-subtitle 完整打包 jianying-subtitle@0.2.3 的 npm 分发内容，含 src/ 源码、dist/ 编译产物、CLI、package.json 与原 GPL-3.0-only LICENSE。执行本包内的 Node CLI，无需外部 Skill、GitHub 下载或 npm 安装。用户可直接查看和修改包内源码；发布和分发遵守该依赖许可证。自有代码 MIT 不改变该目录的 GPL 许可。音频仍会上传字节服务器，内部接口可用性不受本 Skill 保证。

Python runtime 依赖固定在 requirements.txt；FFmpeg/imageio-ffmpeg、字体、音乐及剪映效果资源各自遵守原许可。工程工具的许可不授予音乐/字体/效果的商用权。Skill 不分发剪映 DLL 或安装包。
