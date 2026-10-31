# PaperBridge 浏览器扩展（Chrome / Microsoft Edge）

把浏览器当前打开的 PDF 交给本地 PaperBridge 服务解析，完成后自动打开
HTML 阅读页（翻译仍由沉浸式翻译负责）。

## 构建

```bash
node build.mjs          # 生成 dist/chrome 与 dist/edge
node build.mjs --suffix=Dev   # 带后缀名
```

## 安装（侧载）

1. 打开 `chrome://extensions`（Chrome）或 `edge://extensions`（Edge）。
2. 开启"开发者模式"。
3. 点击"加载已解压的扩展程序"，选择 `dist/chrome`（或 `dist/edge`）。
4. 如需导入本地 `file://` PDF：在扩展详情页开启"允许访问文件网址"。

## 使用

1. 先运行本地服务：`paperbridge serve`。
2. 打开扩展 Web 界面 `http://127.0.0.1:8765/`，点击"生成配对码"。
3. 点击浏览器工具栏 PaperBridge 图标 → 展开"配对本地服务"→ 输入配对码。
4. 打开任意 PDF 标签页，点击插件"解析当前 PDF"；或右键 PDF 链接
   → "用 PaperBridge 解析此链接的 PDF"；或"选择本地 PDF…"。

## 安全设计

- Manifest V3，默认仅申请 `activeTab`、`storage`、`contextMenus`。
- 不申请 `cookies`、`<all_urls>`；站点权限按需申请。
- `webRequest` 为可选权限，只读记录 PDF 响应类型，不拦截请求。
- 所有浏览器接口必须携带本地服务下发的配对令牌。

## Chrome / Edge 差异

- 共享同一份源码（`src/`），`build.mjs` 生成两个侧载包。
- 商店元数据、扩展 ID、更新地址差异由 `manifest.{chrome,edge}.json`
  覆盖（当前默认相同；上架前补充各商店元数据与 privacy policy）。
