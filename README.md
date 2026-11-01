# 天书翻译官 · Tian Shu Translator

把授课 PDF 或幻灯片导出的 PDF 写成零基础中文自学读物。正式成品只有一个完整课程文件夹，打开其中的 `index.html` 即可离线阅读：索引只显示课程标题和各章入口，点击在新标签页打开独立章节，索引页 URL 与滚动位置保留。前置术语在新标签页打开小课堂，正文位置不变，提示与答案默认收起。课件原题的条件、附图、选项与全部小问均须保留，题目来源和答案来源分别标注，知识点与原题分别核销覆盖。

## 安装到 Codex（项目级）

将本技能文件夹放到课程项目的 `.agents/skills/` 下：

```text
课程项目/
├── .agents/skills/tian-shu-translator/
│   ├── SKILL.md
│   ├── README.md
│   ├── requirements.txt
│   ├── scripts/
│   ├── assets/
│   ├── references/
│   ├── examples/
│   └── tests/
└── 课件.pdf
```

Windows PowerShell 示例（压缩包已下载到课程项目 `D:\Study\Course`）：

```powershell
cd D:\Study\Course
New-Item -ItemType Directory -Force .agents\skills | Out-Null
Expand-Archive -Path .\tian-shu-translator.zip -DestinationPath .\.agents\skills -Force
```

在课程项目中打开 Codex 并重新开启会话。依赖只安装在制作端；读者无需 Python。

## 课程制作

对 Codex 说：

> 请使用 tian-shu-translator，把当前文件夹的课程 PDF 做成零基础中文自学读物，只交付一个完整课程文件夹，打开里面的 index.html 即可离线阅读。用自然段讲清本课，index 只显示课程标题和章节索引，点击章节在新标签页打开独立 HTML；章节左侧只列本章小节。前置术语在新标签页打开小课堂，正文位置不变。完整纳入原题的条件、图、选项与全部小问，标明题目和答案来源，提示与答案默认折叠。分别核对知识点与原题覆盖，不要只给概要。

**以下命令均从课程项目当前目录运行。先确定实际加载的 `SKILL.md` 位置，再将其所在目录设为 `$skillRoot`。** 项目级安装的真实结构为：

```powershell
$skillRoot = (Resolve-Path '.agents/skills/tian-shu-translator').Path
python -m pip install -r "$skillRoot/requirements.txt"
```

如果技能安装在其他位置，按实际加载的 `SKILL.md` 所在目录设置 `$skillRoot`，不要切换课程项目的当前目录。`scripts/`、`requirements.txt`、模板和资源属于技能目录；输入 PDF 与下列相对输出路径属于课程项目。已准备虚拟环境时，用该环境的 Python 替换 `python`。

先提取文字并渲染全部源页，供实际查看核对：

```powershell
New-Item -ItemType Directory -Force 'output/课程名/_work/sources' | Out-Null
python "$skillRoot/scripts/extract_pdf.py" '课件.pdf' --out 'output/课程名/_work/sources/课件.json' --render 'output/课程名/_work/sources/pages' --dpi 144
```

多份 PDF 分别提取，使用不同的 JSON 文件和 PNG 目录，避免覆盖。`--render DIR` 会把**所有页**渲染为 `page-0001.png`、`page-0002.png` 等；`--dpi` 接受正整数，默认 144，可按清晰度与体积需要调整。144 DPI 下页面宽高分别是 72 DPI 的两倍。省略 `--render` 时只输出文字 JSON，不生成页面图。

传入 `--render` 后，JSON 顶层 `render_dpi` 记录分辨率，各页的 `pages[].rendered_image` 记录**相对 JSON 所在目录的 POSIX 路径**。例如 JSON 位于课程内 `_work/sources/课件.json`，字段值 `pages/page-0001.png` 对应课程内 `_work/sources/pages/page-0001.png`；它不相对课程项目或技能目录。覆盖台账中的文件路径则统一相对课程文件夹，填写前须换算。

生成 PNG 只是准备材料，**不代表已查看或已视觉核对**。实际查看公式、图表、扫描页、题目布局和提取异常页后，才在 [覆盖与进度模板](references/coverage-template.md) 的页面记录中更新核对状态。完整阅读材料、建立双覆盖台账，再按 [写作母版](references/textbook-template.md) 写入 `output/课程名/_work/课程.md`；辨认不了的内容保留位置和疑点，不猜补。

完成母稿后编译：

```powershell
python "$skillRoot/scripts/compile_html.py" 'output/课程名/_work/课程.md' 'output/课程名' --title '课程名'
```

默认成品结构：

```text
output/课程名/
├── index.html
├── chapters/
│   ├── chapter-01.html
│   └── chapter-02.html
├── lessons/                  有小课堂时生成
└── _work/
    ├── 课程.md
    ├── sources/              提取 JSON 与源页 PNG
    ├── images/               原图素材
    ├── coverage.md           双覆盖与核对记录
    └── progress.md           进度、最后完成位置与下一步
```

长课按章制作，持续保存工作资料并自动续做；不需要逐章发送“继续”。移动或转发整个课程文件夹即可，也可整体压缩。正式回复只给课程文件夹路径或链接和一句“打开里面的 `index.html`”，不另列各章节、小课堂或台账。

## 编译接口与成品核对

命令接口是 `input.md OUTPUT_FOLDER --title '课程名'`，第二个参数是目录；Python 公共接口是 `build_course(source, output_folder, title=None)`。入口固定为输出目录内的 `index.html`，只显示课程标题和各章索引列表，不显示导言、章节正文、全课侧栏或小课堂。所有索引章节链接使用 `target="_blank" rel="noopener noreferrer"`，在新标签页打开，索引页 URL 与滚动位置始终保留。

母稿顶层 `##` 依次生成独立章节；`###` 写本章自然子话题，不机械凑结构，题内细节与答案内标题用 `####` 及以下。章节正文位于视口中央，左侧留白中的小字号目录只链接本章 `h3` 的本页锚点，随阅读滚动高亮；窄屏在顶部折叠。页尾“上一章／课程目录／下一章”保留，其中上一章／下一章在当前标签页切章；正文中显式跨章引用也可在当前标签页打开。小课堂放在 `lessons/`，继续在新标签页打开。

`#` 课程标题后、第一个 `##` 前的导言由编译器完整移到第一章标题后，索引页不显示；没有任何 `##` 的短母稿兼容为名为“正文”的单章，索引页仍只列章节入口。章节结构、公式、图片与 HTML 交互块的完整写法集中在 [写作母版](references/textbook-template.md)。

编译器不复制母稿，所以制作时须直接保存在课程 `_work/` 内。本地图片路径相对输入母稿目录；编译时图片、CSS、JavaScript 与 MathML 均内联。核心阅读不依赖网络、文件夹外的路径或原 PDF。编译器会拒绝重复 ID、失效锚点、缺失或远程图片、拆页后失效的 SVG 资源引用，以及缺少解析或提前展开答案的练习；上述结构检查失败时不会覆盖已有成品。这些检查不证明内容正确或全覆盖。

交付前按 [覆盖与进度模板](references/coverage-template.md) 逐项核销，真实打开成品检查排版和交互，移动整个文件夹后验证离线阅读。记录未执行和未通过的项目，将来源范围、双覆盖结果与必要的缺口说明纳入 HTML，不把未知或未核对项目记作完成。

## 维护者开发与验证

**本节单独从技能仓库根目录运行，与上面的课程制作目录约定不同。** 不把 `examples/` 示例当作真实课程或覆盖证据。

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/compile_html.py examples/vector-notes.md examples/vector-course --title '向量自学读物示例'
```

打开 `examples/vector-course/index.html`，按覆盖模板中的 HTML 检查项核对三个独立章节及小课堂。示例母稿仍为 `examples/vector-notes.md`，只演示排版与交互。仓库内有 `.venv` 时可用 `./.venv/Scripts/python.exe` 替换 `python`。

维护者可另运行真实浏览器检查：

```powershell
python tests/verify_reader_browser.py
```

该检查需要额外安装 Playwright 并有本机 Edge；学生阅读无需这些工具。自动检查通过仍须对照真实课件查看内容与页面，不能替代覆盖核销和必要的视觉判断。
