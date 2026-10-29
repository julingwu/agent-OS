/* Required Notice: Copyright (c) 2026 赛博老爸 (PolyForm Noncommercial License 1.0.0) — Adapted & Wired for AgentOS */

export const HQ_TEMPLATE_HTML = '<style>\n/* Layout（框体极简：左右不放框，公司总进度等主数据直接写在背景上；部门 / 项目 / 成员 / 流水 / 档案收进底部一条）: 行政总部作战大屏 —— 一家公司 = 一座单层「回字形」办公楼（只画这栋楼，深色背景，不画周边环境）：正中是总经理办公室，Chief 坐在大班台后（点它派新任务），\n   北边 4 个部门、东西两个大房间、南边两个部门 + 大堂（会议室 / 前台 / 茶水间）；人事部负责增删改成员。每个人 = 一个 Agent 或一位真人督办；工单从调度中心派出，在各部门之间一棒一棒传下去，真人负责审核放行。\n   框体做得简约：左栏公司总览 + 部门进度，右栏项目进度 + 动态，底部按视图切换（人员看板 / 部门成员 / 项目流水 / 个人档案）。\n   左上标题、右上按钮与前几款同一套写法。3D 是原生 WebGL（阴影、昼夜、移轴、泛光），不依赖 three.js。 */\n:host{\n  color-scheme:dark;\n  --bg:#0b0907; --panel:rgba(16,13,10,.8); --card:rgba(255,240,220,.035);\n  --line:rgba(255,226,186,.09); --line-2:rgba(255,226,186,.2);\n  --fg:#f4ece0; --fg-2:#b9ad9c; --fg-3:#7d7163;\n  --hi:#ffc46b; --ai:#6fdcff; --hu:#ffd060; --ok:#5fe3a1; --warn:#ffb340; --bad:#ff5a4f;\n  --display:"Syncopate","Noto Sans SC",system-ui,sans-serif;\n  --mono:"JetBrains Mono","Noto Sans SC","PingFang SC",ui-monospace,Menlo,monospace;\n  --cjk:"Noto Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif;\n}\n:host{display:block;height:100%;position:relative;overflow:hidden;background:var(--bg);color:var(--fg);font-family:var(--mono);-webkit-font-smoothing:antialiased}\n[hidden]{display:none!important}\n*{box-sizing:border-box}\n.app{position:absolute;inset:0;overflow:hidden}\n.gl{position:absolute;inset:0;width:100%;height:100%;display:block;cursor:grab;touch-action:none}\n.gl.drag{cursor:grabbing}\n.gl.pick{cursor:pointer}\n.fx{position:absolute;inset:0;width:100%;height:100%;display:block;pointer-events:none}\n.vig{position:absolute;inset:0;pointer-events:none;background:radial-gradient(ellipse 78% 82% at 50% 50%,transparent 62%,rgba(0,0,0,.38) 100%)}\n.nr{position:absolute;inset:0;pointer-events:none}\n.fit{position:absolute;inset:0;padding-left:env(safe-area-inset-left,0px);padding-right:env(safe-area-inset-right,0px);pointer-events:none}\n.narrow .fit{padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}\n.fit-in{position:relative;width:100%;height:100%}\n.board{position:absolute;left:50%;top:50%;width:1920px;height:1080px;transform:translate(-50%,-50%) scale(var(--s,.5));pointer-events:none}\n.pe{pointer-events:auto}\n\n/* ---------- 左上标题（与前几款同款） ---------- */\n.topfade{position:absolute;left:-40px;top:0;width:900px;height:280px;background:radial-gradient(ellipse 720px 270px at 0 0,rgba(9,7,5,.92),rgba(9,7,5,.6) 55%,rgba(9,7,5,0) 88%)}\n.botfade{position:absolute;left:0;right:0;bottom:0;height:300px;background:linear-gradient(0deg,rgba(6,8,11,.96),rgba(6,8,11,.88) 55%,rgba(6,8,11,.45) 80%,transparent)}\n.hd{position:absolute;left:10px;top:18px;width:max-content;text-shadow:0 0 6px var(--bg),0 0 14px var(--bg)}\n.eb{width:max-content;font-size:10px;letter-spacing:.26em;color:var(--fg-3);white-space:nowrap}\n.eb b{color:var(--hi);font-weight:500}\n.hd h1{margin:12px 0 0;font-family:var(--display);font-weight:700;font-size:44px;line-height:1.06;letter-spacing:.05em;color:var(--fg)}\n.hd h1 span{display:block;width:max-content;white-space:nowrap}\n.hd h1 .l1{display:flex;align-items:center;width:max-content}\n.hd h1 em{font-style:normal;font-family:var(--cjk);font-weight:900;font-size:36px;line-height:1;letter-spacing:.08em;margin-left:22px;text-shadow:none;background:linear-gradient(90deg,#ffe3a3,#ff9a62);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;filter:drop-shadow(0 0 10px rgba(255,170,100,.3))}\n.sub{width:max-content;margin-top:12px;font-family:var(--cjk);font-size:14px;letter-spacing:.06em;color:var(--fg-2);white-space:nowrap}\n.sub b{color:var(--ai);font-weight:700}\n.sub i{font-style:normal;color:var(--hu);font-weight:700}\n.sub2{width:max-content;margin-top:12px;font-size:10px;letter-spacing:.16em;color:var(--fg-3);white-space:nowrap}\n.sub2 b{color:var(--fg);font-weight:500;font-variant-numeric:tabular-nums}\n\n/* ---------- 右上按钮（与前几款同款） ---------- */\n.tools{position:absolute;right:10px;top:14px;display:flex;gap:6px}\nbutton.tb{font:500 11px/1 var(--mono);letter-spacing:.1em;color:var(--fg-2);background:rgba(14,11,8,.82);border:1px solid rgba(255,226,186,.26);padding:8px 11px;cursor:pointer;white-space:nowrap}\nbutton.tb b{color:var(--fg);font-weight:700;font-family:var(--cjk);margin-left:6px;letter-spacing:.04em}\nbutton.tb:hover{border-color:var(--hi);color:var(--fg)}\nbutton.tb:focus-visible{outline:1px solid var(--hi);outline-offset:2px}\n.strip{position:absolute;right:10px;top:52px;display:flex;gap:16px;padding:5px 10px;background:rgba(14,11,8,.74);border:1px solid var(--line);font-size:9.5px;letter-spacing:.12em;color:var(--fg-3);white-space:nowrap;text-shadow:0 0 6px var(--bg)}\n.strip b{color:var(--fg);font-weight:500;margin-left:5px;font-variant-numeric:tabular-nums}\n.strip .ai b{color:var(--ai)}\n.strip .hu b{color:var(--hu)}\n.strip .live::before,.live-dot::before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--ok);margin-right:6px;vertical-align:1px;box-shadow:0 0 6px var(--ok)}\n\n/* ---------- 顶部中间：视图导航 + 昼夜时钟 ---------- */\n.crumb{position:absolute;left:50%;top:16px;transform:translateX(-50%);display:flex;align-items:stretch;gap:6px;white-space:nowrap}\n.cr{display:flex;align-items:center;gap:9px;height:38px;padding:0 14px;background:rgba(14,11,8,.84);border:1px solid var(--line-2);border-radius:4px;backdrop-filter:blur(6px);font-size:11px;letter-spacing:.08em;color:var(--fg-2)}\n.cr b{font-family:var(--cjk);font-size:15px;font-weight:700;color:var(--fg);letter-spacing:.04em}\n.cr .big{font-family:var(--mono);font-size:20px;font-weight:700;color:var(--hi);letter-spacing:0}\n.cr em{font-style:normal;font-size:10px;letter-spacing:.18em;color:var(--fg-3)}\n.cr .cr-n{font-size:12px;font-weight:700;color:var(--c,var(--hi))}\n.cr .pc{font-family:var(--mono);font-size:15px;font-weight:700;color:var(--c,var(--hi))}\n.cr-sel{border-color:var(--c,var(--line-2));box-shadow:inset 0 -2px 0 var(--c,transparent)}\n.cb{font:600 11px/1 var(--cjk);letter-spacing:.06em;color:var(--fg-2);background:transparent;border:1px solid rgba(255,226,186,.2);border-radius:3px;padding:6px 9px;cursor:pointer}\n.cb:hover{color:var(--fg);border-color:var(--hi)}\n.cb.hi{color:#170f04;background:var(--hi);border-color:var(--hi)}\n.cb.hi:hover{background:#ffd590}\n.ph .cb{padding:4px 8px;font-size:10px;margin-left:10px;pointer-events:auto}\n.cb:focus-visible,.tod:focus-visible{outline:1px solid var(--hi);outline-offset:2px}\n.tag-ai,.tag-hu{display:inline-block;font:700 8.5px/1 var(--mono);letter-spacing:.1em;padding:3px 5px;border-radius:2px}\n.tag-ai{color:#06161c;background:var(--ai)}\n.tag-hu{color:#1c1404;background:var(--hu)}\n.tod{display:flex;align-items:center;gap:8px;height:38px;padding:0 12px;background:rgba(14,11,8,.84);border:1px solid var(--line-2);border-radius:4px;backdrop-filter:blur(6px);color:var(--fg-2);font:500 10px/1 var(--mono);letter-spacing:.1em;cursor:pointer}\n.tod i{font-style:normal;font-size:15px;color:#ffd257;width:16px;text-align:center}\n.tod b{font-size:16px;font-weight:700;color:var(--fg);font-variant-numeric:tabular-nums;letter-spacing:.02em}\n.tod span{font-family:var(--cjk);font-size:11px;color:var(--fg-2);letter-spacing:.04em}\n.tod:hover{border-color:var(--hi)}\n.ov .cr-dp,.ov .cr-pj,.ov .cr-ps,.dp .cr-ov,.dp .cr-pj,.dp .cr-ps,.pj .cr-ov,.pj .cr-dp,.pj .cr-ps,.ps .cr-ov,.ps .cr-dp,.ps .cr-pj{display:none}\n\n/* ---------- 数据不加框：主数据直接写在背景上，其余收进底部一条 ---------- */\n.bar{display:block;height:3px;border-radius:2px;background:rgba(255,255,255,.08);position:relative;overflow:hidden}\n.bar i{position:absolute;left:0;top:0;bottom:0;width:0;border-radius:2px;background:var(--c,var(--hi));transition:width .2s linear}\n.pill{display:inline-block;font:700 9px/1 var(--cjk);letter-spacing:.06em;padding:3px 5px;border:1px solid currentColor;border-radius:3px;white-space:nowrap;font-style:normal}\n.s-ok{color:var(--ok)} .s-warn{color:var(--warn)} .s-bad{color:var(--bad)} .s-dim{color:var(--fg-3)} .s-done{color:var(--hi)}\n.pseg{display:flex;gap:3px;height:5px}\n.pseg i{position:relative;flex:1;border-radius:2px;background:rgba(255,255,255,.08);overflow:hidden}\n.pseg i u{position:absolute;left:0;top:0;bottom:0;width:0;background:var(--c);text-decoration:none}\n.bk{font-family:var(--cjk);font-size:13px;letter-spacing:.12em;color:var(--fg-2);white-space:nowrap}\n.bk em{font-style:normal;font-family:var(--mono);font-size:10px;letter-spacing:.16em;color:var(--fg-3);margin-left:10px}\n/* 左侧：公司总进度大数字 + 6 个指标（纯文字，无底框） */\n.bgl{position:absolute;left:14px;top:200px;width:310px;text-shadow:0 0 10px var(--bg),0 0 24px var(--bg)}\n.bgl::before,.bgr::before{content:"";position:absolute;left:-90px;top:-70px;right:-90px;bottom:-70px;background:radial-gradient(closest-side,rgba(6,8,11,.5),rgba(6,8,11,0));z-index:-1;pointer-events:none}\n.bv{margin-top:4px;font-size:118px;font-weight:800;line-height:.9;letter-spacing:-.05em;color:var(--hi);font-variant-numeric:tabular-nums;white-space:nowrap;text-shadow:0 0 36px rgba(255,196,107,.22)}\n.bv small{font-size:36px;font-weight:600;letter-spacing:0;color:var(--fg-2);margin-left:6px}\n.bgl .pseg{margin-top:16px;width:290px}\n.bs{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px 20px;margin-top:26px;width:290px}\n.bs div{display:flex;flex-direction:column;gap:4px;min-width:0}\n.bs b{font-size:28px;font-weight:700;line-height:1;color:var(--fg);font-variant-numeric:tabular-nums;white-space:nowrap}\n.bs b small{font-size:12px;color:var(--fg-3);margin-left:3px;font-weight:500}\n.bs span{font-family:var(--cjk);font-size:11.5px;letter-spacing:.06em;color:var(--fg-3);white-space:nowrap}\n.bs .w b{color:var(--warn)}\n/* 右侧：动态（几行文字，越旧越淡） */\n.bgr{position:absolute;right:14px;top:104px;width:330px;text-align:right;text-shadow:0 0 8px var(--bg),0 0 18px var(--bg)}\n.log{margin-top:12px;display:flex;flex-direction:column;gap:7px;font-size:11.5px;line-height:1.4}\n.lr{display:flex;justify-content:flex-end;align-items:baseline;gap:8px;white-space:nowrap;opacity:var(--o,1)}\n.lr span:first-child{order:3;font-family:var(--mono);font-size:9.5px;color:var(--fg-3)}\n.lr .a{order:2;color:var(--c,var(--hi));font-family:var(--cjk);font-weight:700}\n.lr .x{order:1;color:var(--fg-2);font-family:var(--cjk);overflow:hidden;text-overflow:ellipsis;max-width:220px}\n.lr.fresh .x{color:var(--fg)}\n.lr.w .x{color:var(--warn)} .lr.r .x{color:#ff8f86} .lr.g .x{color:#ffe0a0}\n/* 底部：部门一行 + 按视图切换的一行（项目 / 成员 / 流水 / 档案） */\n.dock{position:absolute;left:14px;right:14px;bottom:30px;height:200px;display:flex;flex-direction:column;gap:6px}\n.dk-h{flex:none;display:flex;align-items:center;gap:10px;height:22px;font-size:10px;letter-spacing:.14em;color:var(--fg-3);white-space:nowrap}\n.dk-t{display:flex;align-items:baseline;gap:8px}\n.dk-t b{font-family:var(--cjk);font-size:12.5px;color:var(--fg);letter-spacing:.08em}\n.dk-h .sp{flex:1}\n.dk-h2{height:14px;margin-top:4px}\n.board:not(.ov) #plMeta{display:none}\n.dock{text-shadow:0 0 6px rgba(6,8,11,.9)}\n.vt{display:flex;gap:4px;pointer-events:auto}\n.vt button,.sbar button{font:600 10px/1 var(--cjk);letter-spacing:.04em;color:var(--fg-2);background:transparent;border:1px solid rgba(255,226,186,.16);border-radius:3px;padding:5px 9px;cursor:pointer;white-space:nowrap}\n.vt button:hover,.sbar button:hover{border-color:var(--c,var(--hi));color:var(--fg)}\n.vt button.on{color:#170f04;background:var(--hi);border-color:var(--hi)}\n.vt button:focus-visible,.sbar button:focus-visible{outline:1px solid var(--fg);outline-offset:1px}\n.sbar{display:flex;gap:4px;pointer-events:auto}\n.sbar button i{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--c);margin-right:5px;vertical-align:0}\n.sbar button.on{color:#170f04;background:var(--c);border-color:var(--c)}\n.sbar button.on i{background:#170f04}\n.dock .cb{pointer-events:auto;padding:5px 9px;font-size:10px}\n/* 8 个部门 */\n.dl{flex:none;display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:14px;height:60px}\n.dr{position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto;grid-template-rows:auto auto auto;column-gap:8px;row-gap:5px;align-content:start;padding:8px 4px 0;border-top:2px solid var(--c);min-width:0;cursor:pointer;pointer-events:auto;transition:background .25s}\n.dr .nm{display:flex;align-items:baseline;gap:7px;min-width:0;white-space:nowrap}\n.dr .nm b{font-family:var(--cjk);font-size:13.5px;font-weight:700;color:var(--fg)}\n.dr .nm em{display:none}\n.dr .nm span{font-family:var(--cjk);font-size:10px;color:var(--fg-3);overflow:hidden;text-overflow:ellipsis}\n.dr .nm span .tag-ai,.dr .nm span .tag-hu{margin-right:3px;padding:2px 3px;font-size:7.5px;transform:translateY(-1px)}\n.dr .pc{grid-column:2;grid-row:1;font-size:16px;font-weight:700;line-height:1;color:var(--c);font-variant-numeric:tabular-nums}\n.dr .bar{grid-column:1/3;height:2px}\n.dr .ft{grid-column:1/3;display:flex;gap:9px;font-family:var(--cjk);font-size:9.5px;color:var(--fg-3);white-space:nowrap;overflow:hidden}\n.dr .ft b{font-family:var(--mono);font-weight:600;color:var(--fg-2);margin-right:2px}\n.dr .ft .w b{color:var(--warn)}\n.dr:hover,.dr.sel{background:linear-gradient(180deg,rgba(255,240,220,.08),rgba(255,240,220,0))}\n.dr.sel .nm b{color:var(--c)}\n.dr:focus-visible{outline:1px solid var(--fg);outline-offset:-1px}\n.dk-b{flex:1;min-height:0;position:relative}\n.vb{display:none;height:100%;min-height:0}\n.ov .vb-ov,.dp .vb-dp,.pj .vb-pj,.ps .vb-ps{display:flex}\n/* 全景：项目一行 */\n.pl{flex:1;display:flex;gap:14px;min-width:0;overflow-x:auto;scrollbar-width:none;pointer-events:auto}\n.pr{flex:1 0 210px;min-width:0;display:grid;grid-template-columns:minmax(0,1fr) auto;grid-template-rows:auto auto auto;column-gap:8px;row-gap:6px;align-content:start;padding:8px 4px 0;border-top:2px solid var(--c);cursor:pointer;pointer-events:auto;transition:background .25s}\n.pr .nm{display:flex;align-items:baseline;gap:7px;min-width:0;white-space:nowrap}\n.pr .nm u{text-decoration:none;font-size:10px;font-weight:700;color:var(--c)}\n.pr .nm b{font-family:var(--cjk);font-size:13px;font-weight:700;color:var(--fg);overflow:hidden;text-overflow:ellipsis}\n.pr .nm .pill{padding:2px 4px;font-size:8.5px}\n.pr .nm .pill.s-ok{display:none}\n.pr .pc{grid-column:2;grid-row:1;font-size:16px;font-weight:700;line-height:1;color:var(--fg);font-variant-numeric:tabular-nums}\n.pr .stg{grid-column:1/3;display:flex;gap:2px;height:4px}\n.pr .stg i{position:relative;flex:1;border-radius:1px;background:rgba(255,255,255,.08);overflow:hidden}\n.pr .stg i u{position:absolute;left:0;top:0;bottom:0;width:0;background:var(--dc);text-decoration:none;opacity:.9}\n.pr .ft{grid-column:1/3;display:flex;gap:9px;font-family:var(--cjk);font-size:9.5px;color:var(--fg-3);white-space:nowrap;overflow:hidden}\n.pr .ft b{font-family:var(--mono);font-weight:600;color:var(--fg-2);margin-right:2px}\n.pr .ft .r{display:none}\n.pr .ft .cur{overflow:hidden;text-overflow:ellipsis}\n.pr:hover,.pr.sel{background:linear-gradient(180deg,rgba(255,240,220,.08),rgba(255,240,220,0))}\n.pr:focus-visible{outline:1px solid var(--fg);outline-offset:-1px}\n.pr.done .pc{color:var(--hi)}\n/* 部门：成员一行 */\n.mtb{flex:1;display:flex;gap:12px;min-width:0;overflow-x:auto;scrollbar-width:none}\n.mtr{flex:1 0 150px;max-width:260px;min-width:0;display:grid;grid-template-columns:minmax(0,1fr) auto;grid-template-rows:auto auto auto;column-gap:8px;row-gap:6px;align-content:start;padding:8px 4px 0;border-top:2px solid rgba(255,226,186,.14);cursor:pointer;pointer-events:auto}\n.mtr:hover{border-top-color:var(--c);background:linear-gradient(180deg,rgba(255,240,220,.07),rgba(255,240,220,0))}\n.mtr .who{display:flex;align-items:center;gap:6px;min-width:0;white-space:nowrap}\n.mtr .who i.av{flex:none;width:13px;height:13px;border-radius:50%;background:var(--sh);box-shadow:0 0 0 1.5px var(--ring)}\n.mtr .who b{font-family:var(--cjk);font-size:12.5px;color:var(--fg)}\n.mtr .who .tag-ai,.mtr .who .tag-hu{padding:2px 3px;font-size:7.5px}\n.mtr .who em{display:none}\n.mtr .st{font-family:var(--cjk);font-size:10.5px;white-space:nowrap}\n.mtr .tk{grid-column:1/3;font-family:var(--cjk);font-size:10px;color:var(--fg-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n.mtr .tk u{text-decoration:none;font-family:var(--mono);font-weight:700;margin-right:5px}\n.mtr .bar{grid-column:1/3;height:2px}\n.mtr .v{display:none}\n/* 项目：流水线一行 */\n.ppl{flex:1;display:flex;gap:14px;min-width:0}\n.stc{flex:1;display:flex;flex-direction:column;gap:4px;min-width:0;min-height:0;padding:8px 4px 0;border-top:2px solid var(--dc);overflow:hidden}\n.stc h4{margin:0;display:flex;align-items:baseline;gap:6px;white-space:nowrap;font:700 12px/1.2 var(--cjk);color:var(--fg)}\n.stc h4 u{text-decoration:none;font:700 9.5px/1 var(--mono);color:var(--dc)}\n.stc h4 em{font-style:normal;margin-left:auto;font:600 10px/1 var(--mono);color:var(--fg-2)}\n.stc .sd{font-family:var(--cjk);font-size:9.5px;color:var(--fg-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n.stc .ch{display:flex;gap:6px;align-items:baseline;font-family:var(--cjk);font-size:10px;color:var(--fg-2);white-space:nowrap;min-width:0}\n.stc .ch span{overflow:hidden;text-overflow:ellipsis}\n.stc .ch em{font-style:normal;margin-left:auto;font-size:9px;color:var(--fg-3)}\n.stc .ch em.r{color:var(--warn)}\n.stc .ch .bar{display:none}\n.stc .more{font-size:9px;color:var(--fg-3);font-family:var(--cjk)}\n.stc.fin{border-top-color:var(--hi)}\n.stc.fin .big{font-size:24px;font-weight:700;line-height:1;color:var(--hi);font-variant-numeric:tabular-nums}\n/* 人员：档案一行 */\n.pfl{flex:1;display:grid;grid-template-columns:1.25fr .9fr 1.3fr 1.3fr;gap:16px;min-width:0}\n.pfc{display:flex;flex-direction:column;gap:5px;padding:8px 4px 0;border-top:2px solid rgba(255,226,186,.14);min-width:0;min-height:0;overflow:hidden}\n.pf-c1{border-top-color:var(--c)}\n.pfc h5{margin:0;font:600 9px/1 var(--mono);letter-spacing:.14em;color:var(--fg-3)}\n.pf-id{display:flex;align-items:center;gap:9px;min-width:0}\n.pf-id i.av{flex:none;width:24px;height:24px;border-radius:50%;background:var(--sh);box-shadow:0 0 0 2px var(--ring),0 0 12px -2px var(--ring)}\n.pf-id b{display:block;font-family:var(--cjk);font-size:14px;color:var(--fg);white-space:nowrap}\n.pf-id em{display:block;font-style:normal;font-family:var(--cjk);font-size:10px;color:var(--fg-2);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n.pf-sk{display:flex;flex-wrap:nowrap;gap:4px;overflow:hidden}\n.pf-sk span{flex:none;font-family:var(--cjk);font-size:9.5px;color:var(--fg-2);border:1px solid var(--line-2);border-radius:3px;padding:2px 5px}\n.pf-kv{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}\n.pf-kv div{display:flex;flex-direction:column;gap:2px}\n.pf-kv span{font-family:var(--cjk);font-size:9.5px;color:var(--fg-3)}\n.pf-kv b{font-size:16px;color:var(--fg);font-variant-numeric:tabular-nums}\n.pf-now{font-family:var(--cjk);font-size:11.5px;color:var(--fg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n.pf-now u{text-decoration:none;font-family:var(--mono);font-weight:700;margin-right:6px}\n.pf-st{font-family:var(--cjk);font-size:10.5px;color:var(--fg-2)}\n.pf-h{display:flex;flex-direction:column;gap:3px;font-family:var(--cjk);font-size:10px;color:var(--fg-2)}\n.pf-h div{display:grid;grid-template-columns:40px minmax(0,1fr);gap:6px;white-space:nowrap}\n.pf-h div span:first-child{font-family:var(--mono);font-size:9px;color:var(--fg-3)}\n.pf-h div span:last-child{overflow:hidden;text-overflow:ellipsis}\n.pf-h u{text-decoration:none;font-family:var(--mono);font-weight:700;margin-right:5px}\n.pfc .btn.sm{padding:4px 8px;font-size:10.5px}\n\n/* ---------- 3D 上方的标签：部门牌 / 房间牌 / 人员名牌 / 对话气泡 ---------- */\n.labels,.tags,.bubs{position:absolute;inset:0;pointer-events:none}\n.lab{position:absolute;left:0;top:0;opacity:0;display:flex;flex-direction:column;gap:4px;padding:5px 9px 6px 9px;background:rgba(10,11,14,.66);border:1px solid rgba(255,226,186,.1);border-left:3px solid var(--c);border-radius:4px;backdrop-filter:blur(4px);white-space:nowrap;will-change:transform;pointer-events:auto;cursor:pointer;transition:opacity .25s,border-color .3s,box-shadow .3s}\n.lab:hover,.lab.sel{border-color:var(--c);box-shadow:0 0 18px -6px var(--c)}\n.lab .l1{display:flex;align-items:center;gap:7px}\n.lab .l1 b{font-family:var(--cjk);font-size:14px;font-weight:700;color:var(--fg);letter-spacing:.04em}\n.lab .l1 u{text-decoration:none;font-size:9px;letter-spacing:.12em;color:var(--fg-3)}\n.lab .l1 em{font-style:normal;margin-left:auto;padding-left:8px;font-size:13px;font-weight:700;color:var(--c);font-variant-numeric:tabular-nums}\n.lab .bar{width:100%;height:3px}\n.lab .l3{font-family:var(--cjk);font-size:10px;color:var(--fg-2)}\n.lab .l3 b{font-family:var(--mono);color:var(--fg);font-weight:600;margin:0 1px}\n.lab .mg{align-self:flex-start;font:700 10px/1 var(--cjk);color:#170f04;background:var(--c);border:0;border-radius:3px;padding:4px 7px;cursor:pointer}\n.lab.chief{border-left-color:var(--hi);border-color:rgba(255,196,107,.5)}\n.lab.chief .l1 b{color:var(--hi)}\n.lab.rm{border-left-width:2px;padding:4px 8px 5px}\n.lab.rm .l1 b{font-size:12px}\n.lab.rm.on{border-color:var(--c);box-shadow:0 0 16px -6px var(--c)}\n.lab.rm .l3{font-size:9.5px}\n.ov .lab:not(.rm) .l3,.ov .lab.rm:not(.on) .l3{display:none}\n.ov .lab .bar{height:2px}\n.tg{position:absolute;left:0;top:0;opacity:0;display:flex;flex-direction:column;gap:3px;min-width:96px;padding:4px 7px 5px;background:rgba(14,11,8,.88);border:1px solid rgba(255,226,186,.14);border-top:2px solid var(--ring);border-radius:3px;white-space:nowrap;will-change:transform;pointer-events:auto;cursor:pointer;transition:opacity .2s}\n.tg .t1{display:flex;align-items:center;gap:5px}\n.tg .t1 b{font-family:var(--cjk);font-size:11.5px;color:var(--fg)}\n.tg .t1 .tag-ai,.tg .t1 .tag-hu{padding:2px 4px;font-size:7.5px}\n.tg .t1 em{font-style:normal;margin-left:auto;padding-left:6px;font-family:var(--cjk);font-size:9.5px;color:var(--fg-2)}\n.tg .t2{font-family:var(--cjk);font-size:9.5px;color:var(--fg-3);max-width:150px;overflow:hidden;text-overflow:ellipsis}\n.tg .t2 u{text-decoration:none;font-family:var(--mono);font-weight:700;margin-right:4px}\n.tg .bar{height:3px}\n.tg.sel{border-color:var(--fg)}\n.bb{position:absolute;left:0;top:0;opacity:0;max-width:180px;padding:5px 9px;background:#fbf4e6;color:#2a2018;border-radius:9px;font:600 11px/1.35 var(--cjk);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;will-change:transform;box-shadow:0 4px 14px rgba(0,0,0,.35);transition:opacity .25s}\n.bb::after{content:"";position:absolute;left:14px;bottom:-5px;width:10px;height:10px;background:inherit;transform:rotate(45deg);border-radius:2px}\n.bb.ai{background:#e6f9ff;color:#08303b}\n.bb.r{background:#ffe3df;color:#5a1510}\n.bb.g{background:#fff0c9;color:#4a3305}\n\n.sb{position:absolute;left:10px;right:10px;bottom:10px;display:flex;align-items:center;justify-content:center;gap:28px;font-size:9.5px;letter-spacing:.14em;color:var(--fg-3);white-space:nowrap;overflow:hidden}\n.sb b{color:var(--fg-2);font-weight:500;font-variant-numeric:tabular-nums}\n.sb .on::before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--ok);margin-right:7px;vertical-align:1px;box-shadow:0 0 6px var(--ok)}\n.sb .wm{font-family:var(--display);font-size:9px;letter-spacing:.3em;color:#5f5446}\n\n/* ---------- 窄屏 / 竖屏 ---------- */\n.mini{display:none;position:absolute;inset:0;flex-direction:column;justify-content:space-between;pointer-events:none;padding-block:10px}\n.narrow .board{display:none}\n.narrow .mini{display:flex}\n.m-top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;background:linear-gradient(180deg,rgba(9,7,5,.92),rgba(9,7,5,.5) 70%,transparent);margin-inline:-16px;margin-top:-10px;padding:10px 16px 18px}\n.m-title{font-family:var(--display);font-weight:700;font-size:17px;letter-spacing:.05em;line-height:1.1;text-shadow:0 0 8px var(--bg)}\n.m-title i{display:block;font-style:normal;font-family:var(--cjk);font-weight:700;font-size:12px;color:var(--hi);letter-spacing:.12em;margin-top:5px}\n.m-btns{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:5px;max-width:230px}\n.m-btns .tb{padding:6px 8px;font-size:10px}\n.mini .m-back{display:none}\n.mini.in .m-back{display:inline-block;border-color:var(--c,var(--hi));color:var(--fg)}\n.m-bot{background:linear-gradient(0deg,rgba(9,7,5,.95),rgba(9,7,5,.72) 72%,transparent);margin-inline:-16px;padding:26px 16px 0}\n.m-ch{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4px;pointer-events:auto}\n.m-pj{display:flex;gap:4px;overflow-x:auto;scrollbar-width:none;margin-top:6px;pointer-events:auto}\n.m-pj button{flex:none;display:flex;gap:6px;align-items:baseline;border:0;border-left:2px solid var(--c);border-radius:2px;background:rgba(255,255,255,.04);padding:5px 8px;font:inherit;color:var(--fg);cursor:pointer}\n.m-pj b{font-family:var(--cjk);font-size:10px;white-space:nowrap}\n.m-pj em{font-style:normal;font-size:9px;color:var(--c)}\n.m-ch button{display:flex;flex-direction:column;align-items:flex-start;gap:3px;padding:4px 4px 5px;border:0;border-top:2px solid var(--c);border-radius:2px;background:rgba(255,255,255,.04);min-width:0;font:inherit;color:inherit;text-align:left;cursor:pointer}\n.m-ch b{font-family:var(--cjk);font-size:10px;color:var(--fg);font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}\n.m-ch em{font-style:normal;font-family:var(--mono);font-size:9px;color:var(--c);white-space:nowrap}\n.m-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;border-top:1px solid var(--line-2);padding-top:8px;margin-top:8px}\n.m-stats div{display:grid;gap:2px;min-width:0}\n.m-stats span{font-family:var(--cjk);font-size:9.5px;color:var(--fg-3);white-space:nowrap}\n.m-stats b{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n.m-wm{margin-top:6px;font-family:var(--display);font-weight:400;font-size:8.5px;letter-spacing:.3em;color:#5f5446;text-align:center}\n.glfail{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);max-width:380px;text-align:center;font-family:var(--cjk);font-size:13px;line-height:1.7;color:var(--fg-2);border:1px solid var(--line-2);padding:14px 18px;background:rgba(14,11,8,.88);border-radius:6px}\n/* ---------- 弹窗：新增任务（Chief）/ 人员管理（HR）—— 不随画板缩放，手机上也能用 ---------- */\n.mdl{position:absolute;inset:0;z-index:30;display:flex;align-items:center;justify-content:center;background:rgba(8,6,4,.62);backdrop-filter:blur(3px);padding-block:16px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}\n.mdl-card{width:100%;max-width:580px;max-height:100%;display:flex;flex-direction:column;background:#18130e;border:1px solid var(--line-2);border-radius:8px;box-shadow:0 24px 60px rgba(0,0,0,.55);font-family:var(--cjk);color:var(--fg);overflow:hidden}\n.mdl-card.wide{max-width:920px}\n.mdl-h{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;padding:16px 18px 12px;border-bottom:1px solid var(--line)}\n.mdl-h b{display:block;font-size:17px;font-weight:700;letter-spacing:.02em}\n.mdl-h b u{text-decoration:none;color:var(--hi)}\n.mdl-h span{display:block;margin-top:5px;font-size:12px;color:var(--fg-3);line-height:1.5}\n.mdl-x{flex:none;width:30px;height:30px;font:400 20px/1 var(--mono);color:var(--fg-2);background:transparent;border:1px solid var(--line-2);border-radius:5px;cursor:pointer}\n.mdl-x:hover{color:var(--fg);border-color:var(--hi)}\n.mdl-b{flex:1;min-height:0;overflow-y:auto;padding:14px 18px 16px;display:flex;flex-direction:column;gap:14px}\n.mdl-f{display:flex;justify-content:flex-end;align-items:center;flex-wrap:wrap;gap:8px;padding:12px 18px;border-top:1px solid var(--line)}\n.fld{display:flex;flex-direction:column;gap:7px;font-size:12px;color:var(--fg-2);min-width:0}\n.fld>span b{color:var(--hi);font-family:var(--mono);margin-left:4px}\n.fld input[type=text],.fld input[type=search],.fld select,.hr-bar input{font:14px/1.3 var(--cjk);color:var(--fg);background:rgba(255,240,220,.05);border:1px solid var(--line-2);border-radius:5px;padding:9px 10px;min-width:0}\n.fld select option{background:#18130e;color:var(--fg)}\n.fld input:focus,.fld select:focus,.hr-bar input:focus{outline:none;border-color:var(--hi);box-shadow:0 0 0 2px rgba(255,196,107,.18)}\n.fld input[type=range]{accent-color:var(--hi);width:100%}\n.fld.chk{flex-direction:row;align-items:center;gap:8px;font-size:13px;color:var(--fg);cursor:pointer;align-self:end;padding-bottom:9px}\n.fld.chk input{accent-color:var(--hi);width:16px;height:16px}\n.chips{display:flex;flex-wrap:wrap;gap:6px}\n.chip{font:600 12px/1 var(--cjk);color:var(--fg-2);background:transparent;border:1px solid var(--line-2);border-radius:14px;padding:7px 11px;cursor:pointer;white-space:nowrap}\n.chip i{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--c,var(--hi));margin-right:6px;vertical-align:0}\n.chip:hover{border-color:var(--c,var(--hi));color:var(--fg)}\n.chip.on{background:var(--c,var(--hi));border-color:var(--c,var(--hi));color:#160f05}\n.chip.on i{background:#160f05}\n.chain{display:flex;flex-wrap:wrap;align-items:center;gap:6px;font-size:12px;min-height:30px}\n.chain .st{display:inline-flex;align-items:center;gap:6px;padding:6px 9px;border-radius:4px;background:rgba(255,240,220,.05);border-left:3px solid var(--c);white-space:nowrap}\n.chain .st em{font-style:normal;color:var(--fg-3);font-size:11px}\n.chain .st em.h{color:var(--hu)}\n.chain .st em.c{color:var(--hi)}\n.chain .ar{color:var(--fg-3)}\n.chain .none{color:var(--fg-3)}\n.chain .st button{font:400 13px/1 var(--mono);color:var(--fg-3);background:none;border:0;padding:0 0 0 2px;cursor:pointer}\n.row2{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:end}\n.seg{display:inline-flex;align-self:flex-start;border:1px solid var(--line-2);border-radius:5px;overflow:hidden}\n.seg button{font:600 12.5px/1 var(--cjk);color:var(--fg-2);background:transparent;border:0;padding:9px 14px;cursor:pointer}\n.seg button+button{border-left:1px solid var(--line-2)}\n.seg button.on{background:var(--hi);color:#160f05}\n.btn{font:600 13px/1 var(--cjk);color:var(--fg);background:transparent;border:1px solid var(--line-2);border-radius:5px;padding:10px 16px;cursor:pointer;white-space:nowrap}\n.btn:hover{border-color:var(--hi)}\n.btn.pri{background:var(--hi);border-color:var(--hi);color:#160f05}\n.btn.pri:hover{background:#ffd590}\n.btn.dng{color:#ff8f86;border-color:rgba(255,90,79,.55)}\n.btn.sm{font-size:11.5px;padding:6px 9px}\n.btn:focus-visible,.chip:focus-visible,.seg button:focus-visible,.mdl-x:focus-visible,.lnk:focus-visible{outline:2px solid var(--hi);outline-offset:2px}\n.err{font-size:12.5px;color:#ff8f86;line-height:1.5}\n.note{margin-right:auto;font-size:11.5px;color:var(--fg-3)}\n.lnk{font:inherit;color:var(--hi);background:none;border:0;padding:0;cursor:pointer;text-decoration:underline;text-underline-offset:2px}\n.hr-bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}\n.hr-bar .chips{flex:1 1 100%}\n.hr-bar input{flex:1;min-width:140px}\n.hr-tb{display:flex;flex-direction:column;gap:4px}\n.hr-r{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,.9fr) minmax(0,1.3fr) 72px auto;gap:10px;align-items:center;padding:8px 10px;border-radius:5px;background:var(--card);font-size:12.5px;border:1px solid transparent}\n.hr-r .nm{display:flex;align-items:center;gap:8px;min-width:0;white-space:nowrap}\n.hr-r .nm i.av{width:18px;height:18px;border-radius:50%;background:var(--sh);box-shadow:0 0 0 1.5px var(--ring);flex:none}\n.hr-r .nm .tag-ai,.hr-r .nm .tag-hu{flex:none}\n.hr-r .nm em{font-style:normal;font-size:10px;color:#160f05;background:var(--hi);border-radius:3px;padding:2px 5px;flex:none}\n.hr-r .dp{color:var(--c);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n.hr-r .ro{color:var(--fg-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\n.hr-r .st{color:var(--fg-3);white-space:nowrap}\n.hr-r .ac{display:flex;gap:6px;justify-content:flex-end}\n.hr-r.cf{border-color:rgba(255,90,79,.45);background:rgba(255,90,79,.07)}\n.hr-r .cft{grid-column:1/5;color:#ffb3ad;font-size:12px;line-height:1.5}\n.hr-dh{display:flex;align-items:baseline;gap:8px;margin-top:6px;font-size:12px;color:var(--c);font-weight:700}\n.hr-dh em{font-style:normal;font-weight:400;color:var(--fg-3);font-family:var(--mono);font-size:11px}\n.hr-empty{padding:16px;color:var(--fg-3);font-size:12.5px;text-align:center}\n.hf-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px 16px}\n.look{display:flex;align-items:center;gap:10px;flex-wrap:wrap}\n.look i.av{width:30px;height:30px;border-radius:50%;background:var(--sh);box-shadow:0 0 0 2px var(--ring),0 0 0 5px var(--hc);flex:none;margin-right:4px}\n.look em{font-style:normal;font-size:11.5px;color:var(--fg-3)}\n.toastx{position:absolute;left:50%;bottom:60px;transform:translateX(-50%);z-index:25;max-width:calc(100% - 32px);background:rgba(24,19,14,.95);border:1px solid var(--line-2);border-left:3px solid var(--hi);border-radius:5px;padding:9px 14px;font:13px/1.5 var(--cjk);color:var(--fg);pointer-events:none}\n@media (max-width:640px){.row2,.hf-grid{grid-template-columns:1fr}.hr-r{grid-template-columns:minmax(0,1fr) auto}.hr-r .dp,.hr-r .ro,.hr-r .st{display:none}.hr-r .cft{grid-column:1/2}.mdl-card.wide{max-width:100%}}\n@media (prefers-reduced-motion:reduce){.bar i{transition:none}.lab,.tg,.bb,.dr,.pr{transition:none}}\n</style>\n\n<div class="app" id="app">\n<canvas class="gl" id="gl" aria-label="智能总部：一座回字形单层办公楼的沙盘，正中是 Chief 的总经理办公室，四周 8 个部门，每个人是一个 Agent 或一位真人督办。点 Chief 新增任务，点人事部负责人管理成员，点部门或人物看进度；拖动旋转镜头，滚轮缩放，双击复位，Esc 返回"></canvas>\n<canvas class="fx" id="fx" aria-hidden="true"></canvas>\n<div class="vig" aria-hidden="true"></div>\n<div class="nr" id="nr">\n<div class="fit"><div class="fit-in" id="fitIn">\n <div class="board ov" id="board">\n  <div class="topfade"></div>\n  <div class="botfade"></div>\n  <div class="labels" id="labels"></div>\n  <div class="tags" id="tags"></div>\n  <div class="bubs" id="bubs"></div>\n  <header class="hd">\n   <div class="eb">AGENT OPS · THEME <b>10</b> · AGENT HQ · 8 DEPARTMENTS</div>\n   <h1><span>AGENT</span><span class="l1">HQ<em>智能总部</em></span></h1>\n   <div class="sub2"><span id="connTxt">云端引擎已连接 · 实时总线 LIVE · 关窗后台持续运行</span> · <b id="clock">--:--:--</b></div>\n  </header>\n  <nav class="crumb pe" aria-label="视图导航">\n   <div class="cr cr-ov"><span class="live-dot"></span><span class="big" id="crOn">20</span><b>人在忙</b><span>·</span><span class="big" id="crPj">6</span><b>个项目推进中</b><em id="crSub">OVERVIEW</em><button class="cb hi" type="button" data-act="task">＋ 新增任务</button><button class="cb" type="button" data-act="hr">人员管理</button></div>\n   <div class="cr cr-dp cr-sel" id="crDp"><button class="cb" type="button" data-act="home">← 全景</button><span class="cr-n" id="crDn">01</span><b id="crDName">产品部</b><em id="crDEn">PRODUCT</em><span class="pc" id="crDp2">0%</span><button class="cb" type="button" data-act="prev" aria-label="上一个">‹</button><button class="cb" type="button" data-act="next" aria-label="下一个">›</button></div>\n   <div class="cr cr-pj cr-sel" id="crPj2"><button class="cb" type="button" data-act="home">← 全景</button><span class="cr-n" id="crPn">P1</span><b id="crPName">项目</b><span class="pc" id="crPp">0%</span><button class="cb" type="button" data-act="prev" aria-label="上一个">‹</button><button class="cb" type="button" data-act="next" aria-label="下一个">›</button></div>\n   <div class="cr cr-ps cr-sel" id="crPs"><button class="cb" type="button" data-act="back">← 返回</button><span id="crSt"></span><b id="crSName">成员</b><em id="crSRole">ROLE</em><button class="cb" type="button" data-act="prev" aria-label="上一个">‹</button><button class="cb" type="button" data-act="next" aria-label="下一个">›</button></div>\n   <button class="tod" type="button" data-act="tod" aria-label="切换昼夜"><i id="todIc">☀</i><b id="todT">--:--</b><span id="todL">实时</span></button>\n  </nav>\n  <div class="tools pe">\n   <button class="tb" type="button" data-act="mode">视角<b data-f="mode">全景</b></button>\n   <button class="tb" type="button" data-action="open-exchange" title="万界 MCP 交易所 (导出/导入/跨公司雇佣)">💎 MCP 交易所</button>\n   <button class="tb" type="button" data-act="full" data-f="full">全屏</button>\n  </div>\n  <div class="bgl">\n   <div class="bk">公司总进度<em id="cPn">8 个项目加权</em></div>\n   <div class="bv" id="cP">0<small>%</small></div>\n   <div class="pseg" id="cPs"></div>\n   <div class="bs">\n    <div><b id="kDone">0</b><span>今日完成</span></div>\n    <div><b id="kWip">0</b><span>进行中</span></div>\n    <div id="kRevT"><b id="kRev">0</b><span>待真人审核</span></div>\n    <div><b id="kLead">0<small>min</small></b><span>平均交付</span></div>\n    <div><b id="kRw">0<small>%</small></b><span>返工率</span></div>\n    <div><b id="kTok">0</b><span>今日 Token</span></div>\n   </div>\n  </div>\n  <div class="bgr">\n   <div class="bk">动态<em id="logMeta">ALL</em></div>\n   <div class="log" id="log"></div>\n  </div>\n\n  <section class="dock">\n   <div class="dk-h"><span class="dk-t"><b>部门进度</b>DEPARTMENTS</span><span class="sp"></span><span class="sbar" id="sel"></span><span class="vt" id="vt"><button type="button" data-v="ov" class="on">全景</button><button type="button" data-v="dp">部门</button><button type="button" data-v="pj">项目</button><button type="button" data-v="ps">人员</button></span><button class="cb" type="button" data-act="hr" id="btmHR">人员管理</button></div>\n   <div class="dl" id="dl"></div>\n   <div class="dk-h dk-h2"><span class="dk-t"><b id="btmT">项目进度</b><span id="btmE">PROJECTS</span></span><span class="sp"></span><span id="plMeta"></span></div>\n   <div class="dk-b">\n    <div class="vb vb-ov"><div class="pl" id="pl"></div></div>\n    <div class="vb vb-dp"><div class="mtb" id="mtb"></div></div>\n    <div class="vb vb-pj"><div class="ppl" id="ppl"></div></div>\n    <div class="vb vb-ps"><div class="pfl" id="pfl"></div></div>\n   </div>\n  </section>\n\n  <footer class="sb"><span class="wm">DEVELOPED BY @赛博老爸</span></footer>\n </div>\n\n <div class="mini" id="mini">\n  <div class="m-top">\n   <div class="m-title">AGENT HQ<i id="mSub">AgentOS 云端总部 · 实时 LIVE</i></div>\n   <div class="m-btns pe">\n    <button class="tb m-back" type="button" data-act="home">← 全景</button>\n    <button class="tb" type="button" data-act="task">＋任务</button>\n    <button class="tb" type="button" data-act="hr">人员</button>\n    <button class="tb" type="button" data-act="mode">视角<b data-f="mode">全景</b></button>\n    \n   </div>\n  </div>\n  <div class="m-bot">\n   <div class="m-ch" id="mCh"></div>\n   <div class="m-pj" id="mPj"></div>\n   <div class="m-stats"><div><span id="mS1L">公司总进度</span><b id="mS1">0%</b></div><div><span id="mS2L">进行中</span><b id="mS2">0</b></div><div><span id="mS3L">今日完成</span><b id="mS3">0</b></div></div>\n   <div class="m-wm">DEVELOPED BY @赛博老爸</div>\n  </div>\n </div>\n</div></div>\n</div>\n<div class="mdl" id="mdlTask" hidden role="dialog" aria-modal="true" aria-labelledby="tkTitle">\n <div class="mdl-card">\n  <div class="mdl-h"><div><b id="tkTitle">交给 <u>Chief</u> · 新增任务</b><span>Chief 会把任务拆成几项，按部门顺序一棒一棒派下去；带「审」的环节要负责人（真人督办或 Agent）放行。</span></div><button class="mdl-x" type="button" data-close aria-label="关闭">×</button></div>\n  <div class="mdl-b">\n   <label class="fld"><span>任务名称</span><input id="tkName" type="text" maxlength="20" placeholder="例如：新人引导体验优化" autocomplete="off"></label>\n   <div class="fld"><span>任务类型</span><div class="chips" id="tkTpl"></div></div>\n   <div class="fld" id="tkCustomF" hidden><span>自定义流程：点部门依次加入（最多 5 步），每步默认由负责人审核</span><div class="chips" id="tkDepts"></div></div>\n   <div class="fld"><span>流转预览</span><div class="chain" id="tkChain"></div></div>\n   <div class="row2">\n    <label class="fld"><span>拆成几项<b id="tkNv">4</b></span><input id="tkN" type="range" min="2" max="8" value="4"></label>\n    <div class="fld"><span>优先级</span><div class="seg" id="tkPri"><button type="button" data-v="0" class="on">普通</button><button type="button" data-v="1">加急 · 插队先派</button></div></div>\n   </div>\n   <div class="err" id="tkErr" hidden></div>\n  </div>\n  <div class="mdl-f"><button class="btn" type="button" data-close>取消</button><button class="btn pri" type="button" id="tkGo">交给 Chief</button></div>\n </div>\n</div>\n<div class="mdl" id="mdlHR" hidden role="dialog" aria-modal="true" aria-labelledby="hrTitle">\n <div class="mdl-card wide">\n  <div class="mdl-h"><div><b id="hrTitle">人员管理 · <u>HR</u></b><span id="hrMeta">—</span></div><button class="mdl-x" type="button" data-close aria-label="关闭">×</button></div>\n  <div class="mdl-b" id="hrList">\n   <div class="hr-bar"><div class="chips" id="hrDept"></div><input id="hrQ" type="search" placeholder="搜索姓名 / 岗位 / 技能" autocomplete="off" aria-label="搜索成员"><button class="btn pri" type="button" id="hrAdd">＋ 新增成员</button></div>\n   <div class="hr-tb" id="hrTb"></div>\n  </div>\n  <div class="mdl-b" id="hrForm" hidden>\n   <div class="hf-grid">\n    <label class="fld"><span>姓名</span><input id="hfName" type="text" maxlength="8" autocomplete="off" placeholder="两三个字，不能和现有成员重名"></label>\n    <div class="fld"><span>类型</span><div class="seg" id="hfType"><button type="button" data-v="1" class="on">Agent</button><button type="button" data-v="0">真人督办</button></div></div>\n    <label class="fld"><span>部门</span><select id="hfDept"></select></label>\n    <label class="fld"><span>岗位</span><input id="hfRole" type="text" maxlength="14" autocomplete="off" placeholder="例如：前端 Agent"></label>\n    <label class="fld"><span>技能（用顿号或逗号分隔）</span><input id="hfSk" type="text" maxlength="30" autocomplete="off" placeholder="例如：页面、小程序"></label>\n    <label class="fld chk"><input id="hfHead" type="checkbox"><span>设为部门负责人</span></label>\n    <div class="fld"><span>外观</span><div class="look"><i class="av" id="hfAv"></i><button class="btn sm" type="button" id="hfLook">换一套</button><em id="hfSeat">—</em></div></div>\n   </div>\n   <div class="err" id="hfErr" hidden></div>\n  </div>\n  <div class="mdl-f" id="hrFoot"><span class="note" id="hrNote">名单保存在本浏览器 · <button class="lnk" type="button" id="hrReset">恢复默认名单</button></span><button class="btn" type="button" data-close>完成</button></div>\n  <div class="mdl-f" id="hfFoot" hidden><button class="btn" type="button" id="hfCancel">返回列表</button><button class="btn pri" type="button" id="hfSave">保存</button></div>\n </div>\n</div>\n<div class="toastx" id="toastx" hidden></div>\n<div class="glfail" id="glFail" hidden>这台设备的浏览器没法显示 3D 总部（WebGL 不可用），面板数据和人员管理照常可用。换 Chrome / Edge 打开即可看到。</div>\n</div>';

export function mountHQ(host, SR, bridge) {

var destroyed = false;
var rafId = 0;
var clockTimer = 0;
var roInst = null;
bridge = bridge || {};

"use strict";
var TAU = Math.PI * 2;
var RM = false;
try { RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) {}

/* =====================================================================
   1. 配置
      一家公司 = 一座单层「回字形」总部（只画这栋楼，像一块沙盘）：正中是总经理办公室，Chief（总指挥 Agent）坐在大班台后，点它新增任务；
      8 个职能部门各占一个房间，各有负责人（可以是真人督办，也可以是 Agent）；人事部负责增删改成员。
      任务按「模板」拆成几项，按部门顺序一棒一棒传下去；带审核的环节要负责人放行，终审环节交给 Chief。
   ===================================================================== */
var DEPTS = [
  { id: "D1", zh: "产品部", en: "PRODUCT", c: "#ffb547", room: "N1", fn: "需求 · 原型 · 用户研究" },
  { id: "D2", zh: "设计部", en: "DESIGN", c: "#ff7eb6", room: "N2", fn: "界面 · 视觉 · 品牌物料" },
  { id: "D3", zh: "市场部", en: "MARKETING", c: "#ff7a59", room: "N3", fn: "品牌 · 内容 · 推广" },
  { id: "D4", zh: "运营部", en: "OPERATIONS", c: "#4fe0a6", room: "N4", fn: "用户运营 · 客服 · 活动" },
  { id: "D5", zh: "研发部", en: "ENGINEERING", c: "#5cc8ff", room: "W", fn: "前后端 · 算法 · 测试 · 运维" },
  { id: "D6", zh: "人事部", en: "PEOPLE · HR", c: "#b99bff", room: "E", fn: "招聘 · 培训 · 员工关系" },
  { id: "D7", zh: "财务部", en: "FINANCE", c: "#c8e05a", room: "SW", fn: "预算 · 核算 · 税务" },
  { id: "D8", zh: "法务行政部", en: "LEGAL & ADMIN", c: "#e0b48a", room: "SE", fn: "合同 · 合规 · 行政" }
];
var ND = DEPTS.length, HRD = 5;
var CHIEF_C = "#ffc46b";
/* 默认名单：名字 / 岗位 / 部门（-1 = Chief）/ 是否 Agent / 是否负责人 / 发型 / 发色 / 衣服 / 肤色 / 技能 */
var DEF_ROSTER = [
  ["小枢", "Chief Agent · 总指挥", -1, 1, 0, 4, "#7fe3ff", "#f4f1ea", "#f6d8be", ["拆解任务", "派单", "终审"]],
  ["苏然", "产品总监", 0, 0, 1, 3, "#3a2a1e", "#e9dcc4", "#f3cfb0", ["需求评审", "路线图"]],
  ["阿需", "需求分析 Agent", 0, 1, 0, 0, "#5a3a22", "#ffc35c", "#eec39a", ["PRD", "用户故事"]],
  ["图图", "原型 Agent", 0, 1, 0, 1, "#e0569a", "#fbe7c6", "#f6d5b8", ["线框图", "交互稿"]],
  ["问问", "用户研究 Agent", 0, 1, 0, 2, "#2a221c", "#9ad1c4", "#d9a77c", ["访谈", "问卷"]],
  ["墨墨", "设计主管 Agent", 1, 1, 1, 3, "#151515", "#ff8fc0", "#f6d5b8", ["视觉", "设计规范"]],
  ["像素", "UI Agent", 1, 1, 0, 2, "#ffb547", "#f4f1ea", "#eec39a", ["界面", "图标"]],
  ["调色", "视觉 Agent", 1, 1, 0, 1, "#3fc1a8", "#ffd1e3", "#f3cfb0", ["插画", "海报"]],
  ["许诺", "市场总监", 2, 0, 1, 1, "#3a2a1e", "#c8553d", "#f3cfb0", ["品牌", "传播"]],
  ["笔笔", "文案 Agent", 2, 1, 0, 0, "#8a5a33", "#ffd38a", "#f6d5b8", ["文案", "脚本"]],
  ["剪剪", "视频 Agent", 2, 1, 0, 2, "#151515", "#ff8a5c", "#eec39a", ["剪辑", "分镜"]],
  ["阿投", "推广 Agent", 2, 1, 0, 4, "#e0569a", "#f4f1ea", "#d9a77c", ["渠道", "排期"]],
  ["阿运", "运营主管 Agent", 3, 1, 1, 0, "#2a221c", "#4fe0a6", "#eec39a", ["运营策略", "验收"]],
  ["暖暖", "客服 Agent", 3, 1, 0, 3, "#c98d4a", "#bdf2dc", "#f6d5b8", ["答疑", "话术"]],
  ["群群", "社群 Agent", 3, 1, 0, 2, "#3a7bd5", "#ffe08a", "#f3cfb0", ["社群", "互动"]],
  ["乐乐", "活动 Agent", 3, 1, 0, 1, "#ff8a5c", "#e9e4da", "#eec39a", ["活动策划", "玩法"]],
  ["周屹", "技术总监", 4, 0, 1, 0, "#151515", "#2f3a4a", "#eec39a", ["架构", "代码评审"]],
  ["阿前", "前端 Agent", 4, 1, 0, 2, "#3a7bd5", "#7fd3ff", "#f6d5b8", ["页面", "小程序"]],
  ["阿码", "后端 Agent", 4, 1, 0, 0, "#2a221c", "#e9e4da", "#eec39a", ["接口", "数据库"]],
  ["阿算", "算法 Agent", 4, 1, 0, 4, "#6b4bd6", "#c9b8ff", "#f3cfb0", ["推荐", "语义理解"]],
  ["测测", "测试 Agent", 4, 1, 0, 1, "#8a5a33", "#a8e063", "#f6d5b8", ["自动化", "回归"]],
  ["阿维", "运维 Agent", 4, 1, 0, 0, "#151515", "#ff9a62", "#d9a77c", ["发布", "监控"]],
  ["何悦", "HR 总监", 5, 0, 1, 3, "#5a3a22", "#b99bff", "#f6d5b8", ["组织发展", "面试"]],
  ["招招", "招聘 Agent", 5, 1, 0, 1, "#e0569a", "#efe3ff", "#f3cfb0", ["简历筛选", "约面"]],
  ["培培", "培训 Agent", 5, 1, 0, 2, "#3fc1a8", "#ffe08a", "#eec39a", ["课程", "入职引导"]],
  ["陈衡", "财务总监", 6, 0, 1, 0, "#151515", "#3d4658", "#eec39a", ["预算", "内控"]],
  ["算算", "核算 Agent", 6, 1, 0, 4, "#7fe3ff", "#e6f4b8", "#f6d5b8", ["成本核算", "报表"]],
  ["税税", "税务 Agent", 6, 1, 0, 1, "#8a5a33", "#c8e05a", "#d9a77c", ["税务申报", "票据"]],
  ["律言", "法务主管 Agent", 7, 1, 1, 0, "#2a221c", "#e0b48a", "#f3cfb0", ["合同审查", "合规"]],
  ["印印", "行政 Agent", 7, 1, 0, 3, "#c98d4a", "#f4f1ea", "#f6d5b8", ["用章", "会务安排"]],
  ["档档", "档案 Agent", 7, 1, 0, 2, "#3a7bd5", "#f2e2cc", "#eec39a", ["归档", "证照"]]
];
var HAIRS = ["#2a221c", "#151515", "#5a3a22", "#8a5a33", "#c98d4a", "#3a7bd5", "#e0569a", "#6b4bd6", "#3fc1a8", "#ffb547", "#7fe3ff"];
var SHIRTS = ["#ffc35c", "#fbe7c6", "#9ad1c4", "#7fd3ff", "#e9e4da", "#c9b8ff", "#a8e063", "#ff9a62", "#ff8fc0", "#ffd1e3", "#ffd38a", "#bdf2dc", "#ffe08a", "#efe3ff", "#c8e05a", "#3d4658", "#2f3a4a", "#c8553d"];
var SKINS = ["#f6d5b8", "#eec39a", "#f3cfb0", "#d9a77c", "#f6d8be", "#f2cba6"];
/* 任务模板：阶段 = [部门（-1 = Chief）, 名称, 审核（0 不审 / 1 负责人审 / 2 Chief 审）, 时长下限, 时长上限] */
var TPL = [
  { k: "feature", zh: "产品功能", S: [[0, "需求文档", 1, 10, 15], [1, "界面设计", 1, 9, 13], [4, "开发", 0, 12, 18], [4, "测试", 1, 7, 11], [3, "上线运营", 0, 6, 10]],
    parts: ["主流程", "设置页", "消息提醒", "空状态", "数据埋点", "新手引导", "多端适配", "权限控制"] },
  { k: "campaign", zh: "营销活动", S: [[2, "活动方案", 1, 9, 13], [1, "物料设计", 0, 8, 12], [6, "预算核对", 1, 5, 8], [3, "活动执行", 0, 8, 12]],
    parts: ["主视觉", "落地页", "短视频", "社群物料", "海报", "推送文案", "分享会提纲", "邀请函"] },
  { k: "hire", zh: "招聘需求", S: [[5, "岗位 JD", 1, 6, 9], [5, "简历筛选", 0, 8, 12], [4, "业务面试", 1, 6, 9], [7, "入职材料", 0, 5, 8]],
    parts: ["前端工程师", "测试工程师", "视觉设计", "内容运营", "客服专员", "数据分析", "行政助理", "产品经理"] },
  { k: "contract", zh: "合同审查", S: [[7, "条款审查", 1, 8, 12], [6, "财务复核", 1, 6, 9], [-1, "Chief 终审", 2, 3, 5]],
    parts: ["主合同", "补充协议", "保密协议", "附件清单", "授权书", "合作备忘录"] },
  { k: "budget", zh: "预算申请", S: [[3, "需求说明", 0, 6, 9], [6, "预算编制", 1, 8, 12], [-1, "Chief 审批", 2, 3, 5]],
    parts: ["人力预算", "物料预算", "软件工具", "差旅预算", "培训预算", "场地预算"] },
  { k: "content", zh: "内容生产", S: [[2, "脚本文案", 1, 8, 12], [1, "配图设计", 0, 7, 11], [2, "发布排期", 0, 5, 8]],
    parts: ["公众号推文", "短视频脚本", "海报文案", "分享会提纲", "案例故事", "产品手册", "FAQ 合集"] },
  { k: "ops", zh: "运营专项", S: [[3, "运营方案", 1, 8, 12], [0, "数据分析", 0, 7, 10], [3, "用户触达", 0, 8, 12], [7, "合规检查", 1, 5, 8]],
    parts: ["新人任务", "签到活动", "社区开放日", "流失召回", "社群分层", "满意度回访", "成长规则", "客服知识库"] },
  { k: "custom", zh: "自定义流程", S: null, parts: ["第一部分", "第二部分", "第三部分", "第四部分", "第五部分", "第六部分", "第七部分", "第八部分"] }
];
var TPLK = {}; TPL.forEach(function (t, i) { TPLK[t.k] = i; });
/* 开场就在推进的项目 */
var PRESET = [
  { zh: "App 3.0 新版本", tpl: "feature", f: 0.45, T: ["登录页改版", "消息中心", "首页信息流", "个人主页", "搜索联想", "设置页", "夜间模式", "通知推送", "新手引导", "收藏夹"] },
  { zh: "年度品牌活动", tpl: "campaign", f: 0.35, T: ["主视觉", "落地页", "短视频", "社群物料", "海报", "邀请函"] },
  { zh: "春季招聘", tpl: "hire", f: 0.55, T: ["前端工程师", "测试工程师", "视觉设计", "内容运营", "客服专员"] },
  { zh: "合作协议审查", tpl: "contract", f: 0.7, T: ["主合同", "补充协议", "保密协议", "授权书"] },
  { zh: "Q4 部门预算", tpl: "budget", f: 0.25, T: ["研发预算", "市场预算", "运营预算", "人事预算", "行政预算"] },
  { zh: "品牌内容月", tpl: "content", f: 0.6, T: ["公众号推文", "短视频脚本", "海报文案", "案例故事", "产品手册"] },
  { zh: "用户增长季", tpl: "ops", f: 0.3, T: ["新人任务", "签到活动", "社区开放日", "流失召回", "社群分层", "满意度回访"] },
  { zh: "用户成长中心", tpl: "feature", f: 0.12, T: ["成长首页", "等级体系", "成长值明细", "任务中心", "勋章墙", "规则说明"] }
];
var PCOLS = ["#ffd166", "#7bdff2", "#f28482", "#84dcc6", "#f6bd60", "#b8a1ff", "#9ad1ff", "#ffadc6", "#c8e05a", "#ffa07a", "#8fd3c7", "#e0b0ff"];
var PHASE_NM = ["一期", "二期", "三期", "四期", "五期", "六期", "七期", "八期", "九期"];

/* =====================================================================
   2. 通用工具
   ===================================================================== */
/* Math.imul 是 ES2015，旧浏览器没有就用等价的 32 位乘法 */
var imul = Math.imul || function (a, b) { var ah = (a >>> 16) & 0xffff, al = a & 0xffff, bh = (b >>> 16) & 0xffff, bl = b & 0xffff; return ((al * bl) + (((ah * bl + al * bh) << 16) >>> 0)) | 0; };
function mulberry32(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; var t = imul(a ^ a >>> 15, 1 | a); t = t + imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
function clamp(x, a, b) { return x < a ? a : (x > b ? b : x); }
function ease(t) { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); }
function easeIO(t) { t = clamp(t, 0, 1); return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
function lerp(a, b, t) { return a + (b - a) * t; }
function seg(p, a, b) { return clamp((p - a) / (b - a), 0, 1); }
function smooth(a, b, x) { var t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }
function hex3(h) { var n = parseInt(h.slice(1), 16); return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255]; }
function rgba3(c, a) { return "rgba(" + Math.round(c[0] * 255) + "," + Math.round(c[1] * 255) + "," + Math.round(c[2] * 255) + "," + a + ")"; }
function hexA(h, a) { return rgba3(hex3(h), a); }
function mix3(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }
function pad(n, w) { n = String(n); while (n.length < w) n = "0" + n; return n; }
function fmt(n) { return Math.round(n).toLocaleString("en-US"); }
function fmtK(n) { return n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : (n >= 1e4 ? (n / 1e3).toFixed(1) + "k" : String(Math.round(n))); }
function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" }[c]; }); }
function hms(d) { d = d || new Date(); return pad(d.getHours(), 2) + ":" + pad(d.getMinutes(), 2) + ":" + pad(d.getSeconds(), 2); }
function rpick(a) { return a[Math.floor(Math.random() * a.length)]; }
function rr(a, b) { return a + Math.random() * (b - a); }
function ri(a, b) { return Math.floor(rr(a, b + 1)); }
function hyp(a, b) { return Math.sqrt(a * a + b * b); }
function angDiff(a, b) { var d = (b - a) % TAU; if (d > Math.PI) d -= TAU; if (d < -Math.PI) d += TAU; return d; }
function shuffle(a) { for (var i = a.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)), t = a[i]; a[i] = a[j]; a[j] = t; } return a; }
DEPTS.forEach(function (D) { D.col = hex3(D.c); });
function dColor(d) { return d < 0 ? CHIEF_C : DEPTS[d].c; }
function dName(d) { return d < 0 ? "Chief" : DEPTS[d].zh; }

/* =====================================================================
   3. DOM 引用 + 固定结构的面板（部门行 / 部门牌 / 房间牌 / 气泡池 / 窄屏部门入口）
   ===================================================================== */
function $(id) { return SR.getElementById(id); }
var app = $("app"), glc = $("gl"), fxc = $("fx"), board = $("board"), fitIn = $("fitIn"), nr = $("nr"), mini = $("mini");
var logBox = $("log");
function setTxt(el, s) { if (el && el.__t !== s) { el.__t = s; el.textContent = s; } }
function setHTML(el, s) { if (el && el.__h !== s) { el.__h = s; el.innerHTML = s; } }
function setCls(el, c) { if (el && el.__c !== c) { el.__c = c; el.className = c; } }
function setW(el, p) { var s = (Math.round(clamp(p, 0, 1) * 1000) / 10) + "%"; if (el && el.__w !== s) { el.__w = s; el.style.width = s; } }
function setVar(el, k, v) { if (el && el["__" + k] !== v) { el["__" + k] = v; el.style.setProperty(k, v); } }
function badge(p) { return p.ai ? '<i class="tag-ai">AI</i>' : '<i class="tag-hu">真人</i>'; }
function avStyle(p) { return "--sh:" + p.sc + ";--ring:" + (p.ai ? "var(--ai)" : "var(--hu)") + ";--c:" + dColor(p.d) + ";--hc:" + p.hc; }

/* 02 部门进度：8 行（负责人名字随人事调整更新） */
var DR = [];
DEPTS.forEach(function (D, k) {
  var d = document.createElement("div");
  d.className = "dr"; d.style.setProperty("--c", D.c); d.setAttribute("data-d", k); d.setAttribute("role", "button"); d.tabIndex = 0;
  d.innerHTML = '<span class="nm"><b>' + D.zh + "</b><em>" + D.en + '</em><span class="hdn">—</span></span><span class="pc">0%</span><span class="bar"><i></i></span>' +
    '<span class="ft"><span><b class="a">0</b>在忙</span><span><b class="q">0</b>排队</span><span class="w"><b class="r">0</b>待审</span><span><b class="o">0</b>今日完成</span></span>';
  $("dl").appendChild(d);
  DR.push({ el: d, hd: d.querySelector(".nm .hdn"), pc: d.querySelector(".pc"), bar: d.querySelector(".bar i"), a: d.querySelector(".a"), q: d.querySelector(".q"), r: d.querySelector(".r"), o: d.querySelector(".o") });
});
/* 3D 上方的部门牌（人事部带「人员管理」按钮）+ Chief 牌 + 房间牌 */
var LABELS = [];
DEPTS.forEach(function (D, k) {
  var d = document.createElement("div");
  d.className = "lab"; d.style.setProperty("--c", D.c); d.setAttribute("data-d", k);
  d.innerHTML = '<div class="l1"><u>' + pad(k + 1, 2) + "</u><b>" + D.zh + "</b><em>0%</em></div><div class=\"bar\"><i></i></div><div class=\"l3\">—</div>" + (k === HRD ? '<button class="mg" type="button" data-hr>人员管理</button>' : "");
  $("labels").appendChild(d);
  LABELS.push({ el: d, pc: d.querySelector("em"), bar: d.querySelector(".bar i"), l3: d.querySelector(".l3"), vis: false, tf: "" });
});
var CHL = (function () {
  var d = document.createElement("div");
  d.className = "lab chief"; d.style.setProperty("--c", CHIEF_C); d.setAttribute("data-chief", "1");
  d.innerHTML = '<div class="l1"><u>GM</u><b>总经理办公室 · 小枢</b><em>0</em></div><div class="l3">—</div><button class="mg" type="button" data-task>＋ 新增任务</button>';
  $("labels").appendChild(d);
  return { el: d, nm: d.querySelector(".l1 b"), pc: d.querySelector("em"), l3: d.querySelector(".l3"), vis: false, tf: "" };
})();
var ROOMS_UI = [{ id: "meet", zh: "会议室", c: "#ffd36e" }, { id: "lounge", zh: "茶水间", c: "#ffb27a" }, { id: "lobby", zh: "前台 · 大门", c: "#ffb27a" }];
var RLAB = ROOMS_UI.map(function (R, i) {
  var d = document.createElement("div");
  d.className = "lab rm"; d.style.setProperty("--c", R.c); d.setAttribute("data-r", i);
  d.innerHTML = '<div class="l1"><b>' + R.zh + '</b></div><div class="l3">—</div>';
  $("labels").appendChild(d);
  return { el: d, l3: d.querySelector(".l3"), vis: false, tf: "", R: R };
});
/* 对话气泡池 */
var BUBS = [];
for (var bi = 0; bi < 10; bi++) { var bd = document.createElement("div"); bd.className = "bb"; $("bubs").appendChild(bd); BUBS.push({ el: bd, p: null, t: -99, life: 0, vis: false, tf: "", w: 100, h: 26 }); }
/* 窄屏：8 个部门入口 */
var MCH = [];
DEPTS.forEach(function (D, k) { var b = document.createElement("button"); b.type = "button"; b.setAttribute("data-d", k); b.style.setProperty("--c", D.c); b.innerHTML = "<b>" + D.zh + "</b><em>0%</em>"; $("mCh").appendChild(b); MCH.push(b.querySelector("em")); });

/* 04 动态：每条记录带上关联的部门 / 项目 / 人，切视图时按关联筛选 */
var LOG = [], logDirty = true;
function log(a, c, msg, kind, tag) { LOG.unshift({ t: hms(), a: a, c: c, x: msg, k: kind || "", g: tag || {}, fresh: true }); if (LOG.length > 90) LOG.length = 90; logDirty = true; }
var toastT = 0;
function toast(s) { var el = $("toastx"); el.textContent = s; el.hidden = false; clearTimeout(toastT); toastT = setTimeout(function () { el.hidden = true; }, 2600); }

/* =====================================================================
   6. 矩阵与向量
   ===================================================================== */
var I3 = [1, 0, 0, 0, 1, 0, 0, 0, 1];
function v3sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function v3add(a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
function v3sc(a, s) { return [a[0] * s, a[1] * s, a[2] * s]; }
function v3len(a) { return Math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]); }
function v3n(a) { var l = v3len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; }
function v3x(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
function v3dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
function v3lerp(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }
function m3apply(m, v) { return [m[0] * v[0] + m[3] * v[1] + m[6] * v[2], m[1] * v[0] + m[4] * v[1] + m[7] * v[2], m[2] * v[0] + m[5] * v[1] + m[8] * v[2]]; }
function m3mul(a, b) { var o = [], c, r; for (c = 0; c < 3; c++) for (r = 0; r < 3; r++) o[c * 3 + r] = a[r] * b[c * 3] + a[3 + r] * b[c * 3 + 1] + a[6 + r] * b[c * 3 + 2]; return o; }
function rotAxis(ax, a) {
  var x = ax[0], y = ax[1], z = ax[2], c = Math.cos(a), s = Math.sin(a), t = 1 - c;
  return [t * x * x + c, t * x * y + s * z, t * x * z - s * y, t * x * y - s * z, t * y * y + c, t * y * z + s * x, t * x * z + s * y, t * y * z - s * x, t * z * z + c];
}
var AXX = rotAxis([0, 0, 1], -Math.PI / 2), AXZ = rotAxis([1, 0, 0], Math.PI / 2);
function rotZ(a) { var c = Math.cos(a), s = Math.sin(a); return [c, s, 0, -s, c, 0, 0, 0, 1]; }
function rotX(a) { var c = Math.cos(a), s = Math.sin(a); return [1, 0, 0, 0, c, s, 0, -s, c]; }
function rotY(a) { var c = Math.cos(a), s = Math.sin(a); return [c, 0, -s, 0, 1, 0, s, 0, c]; }
function TT(m, t) { return { m: m || I3, t: t || [0, 0, 0] }; }
function tf(T, p) { if (!T) return p; var q = m3apply(T.m, p); return [q[0] + T.t[0], q[1] + T.t[1], q[2] + T.t[2]]; }
function tn(T, n) { return T ? v3n(m3apply(T.m, n)) : v3n(n); }
/* 4×4 列主序 */
function m4mul(a, b) { var o = new Array(16), c, r; for (c = 0; c < 4; c++) for (r = 0; r < 4; r++) o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3]; return o; }
function m4RT(R, t) { return [R[0], R[1], R[2], 0, R[3], R[4], R[5], 0, R[6], R[7], R[8], 0, t[0], t[1], t[2], 1]; }
function m4T(x, y, z) { return m4RT(I3, [x, y, z]); }
function m4S(s) { return [s, 0, 0, 0, 0, s, 0, 0, 0, 0, s, 0, 0, 0, 0, 1]; }
function m4pt(m, p) { return [m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12], m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13], m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14]]; }
function m4persp(fovy, asp, near, far, ox, oy) {
  var f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
  return [f / asp, 0, 0, 0, 0, f, 0, 0, -ox, -oy, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
}
function m4ortho(l, r, b, t, n, f) { return [2 / (r - l), 0, 0, 0, 0, 2 / (t - b), 0, 0, 0, 0, -2 / (f - n), 0, -(r + l) / (r - l), -(t + b) / (t - b), -(f + n) / (f - n), 1]; }
function m4look(eye, tg, up) {
  var z = v3n(v3sub(eye, tg)), x = v3n(v3x(up, z)), y = v3x(z, x);
  return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -v3dot(x, eye), -v3dot(y, eye), -v3dot(z, eye), 1];
}

/* =====================================================================
   7. 网格构建：每个顶点 = 位置 + 法线 + 颜色 + 材质号 + 参数 u（11 个 float，交错存放）
   ===================================================================== */
var MPL = 0, MGRASS = 1, MPAVE = 2, MROAD = 3, MCONC = 4, MWALL = 5, MROOF = 6, MWIN = 7, MGLASS = 8, MBELT = 9, MMET = 10,
  MEMIT = 11, MSCR = 12, MHAZ = 13, MSOLAR = 14, MFLOOR = 15, MWATER = 16, MLEAF = 17, MLAMP = 18, MANDG = 19, MANDA = 20, MANDR = 21,
  MCARD = 22, MSKY = 23, MHILL = 24, MRUB = 25, MACC = 26, MGLOW = 27, MBRICK = 28, MPLAZA = 29, MLEDV = 30, MTAPE = 31, MPAINT = 32, MSLOT2 = 33, MFLOWER = 34, MCARPET = 35, MWOOD = 36;
var CM = null, COL = [0.8, 0.8, 0.8], CU = 0, GT = null, UFN = null;
function lin(c) { return [Math.pow(c[0], 2.2), Math.pow(c[1], 2.2), Math.pow(c[2], 2.2)]; }
function begin() { CM = { D: [], I: [], n: 0 }; return CM; }
function col(c) { COL = lin(typeof c === "string" ? hex3(c) : c); }
function vx(p, n, m) {
  var u = UFN ? UFN(p) : CU;
  if (GT) { p = tf(GT, p); n = tn(GT, n); }
  CM.D.push(p[0], p[1], p[2], n[0], n[1], n[2], COL[0], COL[1], COL[2], m, u);
  return CM.n++;
}
function q4(a, b, c, d) { CM.I.push(a, b, c, a, c, d); }
function quad(p0, p1, p2, p3, n, mat) { q4(vx(p0, n, mat), vx(p1, n, mat), vx(p2, n, mat), vx(p3, n, mat)); }
function tri(p0, p1, p2, n, mat) { CM.I.push(vx(p0, n, mat), vx(p1, n, mat), vx(p2, n, mat)); }
function box(c, s, mat, T) {
  var hx = s[0] / 2, hy = s[1] / 2, hz = s[2] / 2;
  var F = [[[1, 0, 0], [[hx, -hy, -hz], [hx, hy, -hz], [hx, hy, hz], [hx, -hy, hz]]], [[-1, 0, 0], [[-hx, -hy, hz], [-hx, hy, hz], [-hx, hy, -hz], [-hx, -hy, -hz]]],
    [[0, 1, 0], [[-hx, hy, -hz], [-hx, hy, hz], [hx, hy, hz], [hx, hy, -hz]]], [[0, -1, 0], [[-hx, -hy, hz], [-hx, -hy, -hz], [hx, -hy, -hz], [hx, -hy, hz]]],
    [[0, 0, 1], [[-hx, -hy, hz], [hx, -hy, hz], [hx, hy, hz], [-hx, hy, hz]]], [[0, 0, -1], [[hx, -hy, -hz], [-hx, -hy, -hz], [-hx, hy, -hz], [hx, hy, -hz]]]];
  F.forEach(function (f) { var id = f[1].map(function (p) { return vx(tf(T, [p[0] + c[0], p[1] + c[1], p[2] + c[2]]), tn(T, f[0]), mat); }); q4(id[0], id[1], id[2], id[3]); });
}
function rpt(r, y, t) { return [r * Math.cos(t), y, r * Math.sin(t)]; }
/* 车削体：profile 从上往下 [[半径, 高度], ...] */
function lathe(prof, sg, mat, T) {
  var i, j, np = prof.length;
  for (i = 0; i < np - 1; i++) {
    var p0 = prof[i], p1 = prof[i + 1], ll = Math.sqrt((p1[0] - p0[0]) * (p1[0] - p0[0]) + (p1[1] - p0[1]) * (p1[1] - p0[1])) || 1, nr = -(p1[1] - p0[1]) / ll, ny = (p1[0] - p0[0]) / ll;
    for (j = 0; j < sg; j++) {
      var t0 = j / sg * TAU, t1 = (j + 1) / sg * TAU;
      var n0 = [nr * Math.cos(t0), ny, nr * Math.sin(t0)], n1 = [nr * Math.cos(t1), ny, nr * Math.sin(t1)];
      q4(vx(tf(T, rpt(p0[0], p0[1], t0)), tn(T, n0), mat), vx(tf(T, rpt(p0[0], p0[1], t1)), tn(T, n1), mat), vx(tf(T, rpt(p1[0], p1[1], t1)), tn(T, n1), mat), vx(tf(T, rpt(p1[0], p1[1], t0)), tn(T, n0), mat));
    }
  }
}
/* 平滑车削（球、穹顶）：法线按轮廓点算 */
function latheS(prof, nrm, sg, mat, T) {
  var i, j;
  for (i = 0; i < prof.length - 1; i++) for (j = 0; j < sg; j++) {
    var t0 = j / sg * TAU, t1 = (j + 1) / sg * TAU, a = prof[i], b = prof[i + 1], na = nrm[i], nb = nrm[i + 1];
    q4(vx(tf(T, rpt(a[0], a[1], t0)), tn(T, [na[0] * Math.cos(t0), na[1], na[0] * Math.sin(t0)]), mat), vx(tf(T, rpt(a[0], a[1], t1)), tn(T, [na[0] * Math.cos(t1), na[1], na[0] * Math.sin(t1)]), mat),
      vx(tf(T, rpt(b[0], b[1], t1)), tn(T, [nb[0] * Math.cos(t1), nb[1], nb[0] * Math.sin(t1)]), mat), vx(tf(T, rpt(b[0], b[1], t0)), tn(T, [nb[0] * Math.cos(t0), nb[1], nb[0] * Math.sin(t0)]), mat));
  }
}
function sphere(c, r, mat, su, sv, sy) {
  var prof = [], nrm = [], i; sv = sv || 6; sy = sy || 1;
  for (i = 0; i <= sv; i++) { var a = Math.PI / 2 - i / sv * Math.PI; prof.push([Math.max(0.0001, r * Math.cos(a)), r * sy * Math.sin(a)]); nrm.push([Math.cos(a), Math.sin(a)]); }
  latheS(prof, nrm, su || 10, mat, TT(I3, c));
}
function dome(c, r, mat, su, sv) {
  var prof = [], nrm = [], i;
  for (i = 0; i <= sv; i++) { var a = Math.PI / 2 - i / sv * Math.PI / 2; prof.push([Math.max(0.0001, r * Math.cos(a)), r * Math.sin(a)]); nrm.push([Math.cos(a), Math.sin(a)]); }
  latheS(prof, nrm, su, mat, TT(I3, c));
}
function torus(R, r, mat, T, su, sv) {
  su = su || 32; sv = sv || 6;
  var base = CM.n, i, j;
  for (i = 0; i <= su; i++) { var u = i / su * TAU, cu = Math.cos(u), snu = Math.sin(u);
    for (j = 0; j <= sv; j++) { var v = j / sv * TAU, cv = Math.cos(v), snv = Math.sin(v); vx(tf(T, [(R + r * cv) * cu, r * snv, (R + r * cv) * snu]), tn(T, [cv * cu, snv, cv * snu]), mat); } }
  for (i = 0; i < su; i++) for (j = 0; j < sv; j++) { var a = base + i * (sv + 1) + j, b = a + sv + 1; q4(a, b, b + 1, a + 1); }
}
function cyl(c, r, h, mat, m, sg) { lathe([[0.0001, h / 2], [r, h / 2], [r, -h / 2], [0.0001, -h / 2]], sg || 18, mat, TT(m, c)); }
function alignY(d) { d = v3n(d); var ref = Math.abs(d[1]) < 0.95 ? [0, 1, 0] : [1, 0, 0]; var u = v3n(v3x(ref, d)), w = v3x(u, d); return [u[0], u[1], u[2], d[0], d[1], d[2], w[0], w[1], w[2]]; }
function tube(a, b, r, mat, sg) { var d = v3sub(b, a), L0 = v3len(d); lathe([[r, L0 / 2], [r, -L0 / 2]], sg || 10, mat, TT(alignY(d), [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2])); }
function rail(pts, r, mat) { for (var i = 1; i < pts.length; i++) tube(pts[i - 1], pts[i], r, mat, 8); }
function disk(c, r, mat, sg, up) {
  var i, n = up === false ? [0, -1, 0] : [0, 1, 0], ctr = vx(c, n, mat), ring = [];
  for (i = 0; i <= sg; i++) { var a = i / sg * TAU; ring.push(vx([c[0] + r * Math.cos(a), c[1], c[2] + r * Math.sin(a)], n, mat)); }
  for (i = 0; i < sg; i++) { if (up === false) CM.I.push(ctr, ring[i], ring[i + 1]); else CM.I.push(ctr, ring[i + 1], ring[i]); }
}
function ann(c, r0, r1, mat, sg) {
  for (var i = 0; i < sg; i++) { var a = i / sg * TAU, b = (i + 1) / sg * TAU;
    quad([c[0] + r0 * Math.cos(a), c[1], c[2] + r0 * Math.sin(a)], [c[0] + r0 * Math.cos(b), c[1], c[2] + r0 * Math.sin(b)], [c[0] + r1 * Math.cos(b), c[1], c[2] + r1 * Math.sin(b)], [c[0] + r1 * Math.cos(a), c[1], c[2] + r1 * Math.sin(a)], [0, 1, 0], mat); }
}
/* 地面上的平面矩形（x0,z0 → x1,z1） */
function flat(x0, z0, x1, z1, y, mat) { quad([x0, y, z0], [x0, y, z1], [x1, y, z1], [x1, y, z0], [0, 1, 0], mat); }
/* 三色塔灯 */
function andon(x, y0, z) {
  col("#2d3136"); cyl([x, y0 + 0.16, z], 0.018, 0.32, MPL, null, 8);
  col("#ffffff"); cyl([x, y0 + 0.36, z], 0.055, 0.08, MANDG, null, 14); cyl([x, y0 + 0.445, z], 0.055, 0.08, MANDA, null, 14); cyl([x, y0 + 0.53, z], 0.055, 0.08, MANDR, null, 14);
  col("#2d3136"); cyl([x, y0 + 0.585, z], 0.045, 0.03, MPL, null, 14);
}

/* ---------- 折线路径：圆角 + 按弧长取点 ---------- */
function pathMake(P, R) {
  var S = [P[0]], i, k;
  for (i = 1; i < P.length - 1; i++) {
    var a = P[i - 1], b = P[i], c = P[i + 1];
    var d0 = [b[0] - a[0], b[1] - a[1]], d1 = [c[0] - b[0], c[1] - b[1]], l0 = hyp(d0[0], d0[1]), l1 = hyp(d1[0], d1[1]);
    var r = Math.min(R, l0 * 0.45, l1 * 0.45);
    var A = [b[0] - d0[0] / l0 * r, b[1] - d0[1] / l0 * r], B = [b[0] + d1[0] / l1 * r, b[1] + d1[1] / l1 * r];
    for (k = 0; k <= 8; k++) { var t = k / 8, u = 1 - t; S.push([u * u * A[0] + 2 * u * t * b[0] + t * t * B[0], u * u * A[1] + 2 * u * t * b[1] + t * t * B[1]]); }
  }
  S.push(P[P.length - 1]);
  var out = [S[0]];
  for (i = 1; i < S.length; i++) if (hyp(S[i][0] - out[out.length - 1][0], S[i][1] - out[out.length - 1][1]) > 0.01) out.push(S[i]);
  var L = [0];
  for (i = 1; i < out.length; i++) L.push(L[i - 1] + hyp(out[i][0] - out[i - 1][0], out[i][1] - out[i - 1][1]));
  return { S: out, L: L, len: L[L.length - 1] };
}
function pathAt(P, s) {
  s = clamp(s, 0, P.len);
  var lo = 0, hi = P.L.length - 1;
  while (hi - lo > 1) { var m = (lo + hi) >> 1; if (P.L[m] <= s) lo = m; else hi = m; }
  var t = (s - P.L[lo]) / ((P.L[hi] - P.L[lo]) || 1), a = P.S[lo], b = P.S[hi], dx = b[0] - a[0], dz = b[1] - a[1], l = hyp(dx, dz) || 1;
  return { x: a[0] + dx * t, z: a[1] + dz * t, dx: dx / l, dz: dz / l };
}

/* =====================================================================
   8. 总部布局（世界坐标，楼内地面 y = 0，台基底面 y = -0.32；整座楼像一块沙盘放在深色底座上，不画周边环境；镜头默认在东南方向往下看）
      回字形：北边 4 个房间 + 北走廊；中间 = 西大房间 | 西廊 | 总经理办公室（Chief 的大班台）| 东廊 | 东大房间；
      南走廊 + 南边：西南房间 | 大堂（会议室 / 前台 / 茶水间）| 东南房间；大门开在南墙正中
      每个房间有自己的局部坐标 u / v：+v 指向房门那面墙，负责人坐在最里面、面朝房门
   ===================================================================== */
var BX0 = -18, BX1 = 18, BZ0 = -12.5, BZ1 = 12.5, GY = -0.32;
var HALL_N = -5.3, HALL_S = 5.3, COR_W = -6.5, COR_E = 6.5;
var SEAT_H = 0.3, DESK_Y = 0.52, WALL_H = 1.05, EXT_H = 1.35;
var ROOMS = {
  N1: { x0: -18, x1: -9, z0: -12.5, z1: -6.2, door: "S" }, N2: { x0: -9, x1: 0, z0: -12.5, z1: -6.2, door: "S" },
  N3: { x0: 0, x1: 9, z0: -12.5, z1: -6.2, door: "S" }, N4: { x0: 9, x1: 18, z0: -12.5, z1: -6.2, door: "S" },
  W: { x0: -18, x1: -7.4, z0: -4.4, z1: 4.4, door: "E", big: true }, E: { x0: 7.4, x1: 18, z0: -4.4, z1: 4.4, door: "W", big: true },
  SW: { x0: -18, x1: -6, z0: 6.2, z1: 12.5, door: "N", wide: true }, SE: { x0: 6, x1: 18, z0: 6.2, z1: 12.5, door: "N", wide: true }
};
(function () {
  for (var key in ROOMS) {
    var R = ROOMS[key];
    R.key = key; R.cx = (R.x0 + R.x1) / 2; R.cz = (R.z0 + R.z1) / 2;
    R.a = R.door === "S" ? 0 : (R.door === "N" ? Math.PI : (R.door === "E" ? Math.PI / 2 : -Math.PI / 2));
    var side = R.door === "S" || R.door === "N";
    R.hw = side ? (R.x1 - R.x0) / 2 : (R.z1 - R.z0) / 2; R.hd = side ? (R.z1 - R.z0) / 2 : (R.x1 - R.x0) / 2;
    R.T = TT(rotY(R.a), [R.cx, 0, R.cz]);
    R.doorU = R.hw - 1.3;
    R.cl = R.big ? [[-0.6, -1.5], [-0.6, 2.4]] : (R.wide ? [[-1.6, 0.75]] : [[-0.4, 0.55]]);
    R.head = [R.wide ? -1.8 : -0.6, -R.hd + (R.wide ? 0.75 : 0.6)];
  }
})();
/* 房间局部 → 世界（x, z） */
function rw(R, u, v) { var c = Math.cos(R.a), s = Math.sin(R.a); return [R.cx + u * c + v * s, R.cz - u * s + v * c]; }
DEPTS.forEach(function (D) { D.R = ROOMS[D.room]; });
/* 总经理办公室：HOLO = 大班台中心（派单 / 归档特效落在这里），GM_SCR = 北墙大屏，GM_TBL = 东侧 6 人会议桌 */
var HOLO = [0, -2.05], GM_SCR = [0, -4.08], GM_TBL = [3.6, 1.6], GM = { x0: -5.6, x1: 5.6, z0: -4.4, z1: 4.4 };
/* 人脚下的地面高度：楼内 0，楼外园区 GY，大门台阶处一段斜坡 */
function groundY(x, z) {
  if (x > BX0 - 0.3 && x < BX1 + 0.3 && z > BZ0 - 0.3 && z < BZ1 + 0.35) return 0;
  if (Math.abs(x) < 1.8 && z >= BZ1 + 0.35 && z < BZ1 + 1.15) return GY * (z - BZ1 - 0.35) / 0.8;
  return GY;
}
var MEET_C = [-4.1, 9.35];

var RND = mulberry32(2026);
function rn(a, b) { return a + RND() * (b - a); }
function rp(a) { return a[Math.floor(RND() * a.length)]; }
function TR(yaw, x, z) { return TT(rotY(yaw), [x, 0, z]); }
function TRm(T, m) { return TT(m3mul(T.m, m), T.t); }
var GREENS = ["#4f9a3c", "#5fae47", "#3f8a3a", "#6cb24a", "#57a043", "#4a8f45"];
var LAMPS = [];

/* ---------- 背景罩（只画纯色渐变，不画天空和远景） ---------- */
var SKY = begin();
(function () {
  col("#ffffff");
  var prof = [], nrm = [], i;
  for (i = 0; i <= 12; i++) { var a = Math.PI / 2 - i / 12 * Math.PI; prof.push([Math.max(0.01, 300 * Math.cos(a)), 300 * Math.sin(a)]); nrm.push([-Math.cos(a), -Math.sin(a)]); }
  latheS(prof, nrm, 32, MSKY, null);
})();
/* ---------- 家具零件（局部坐标里人坐在 -z 一侧、面朝 +z） ---------- */
function desk(T, w, d, top, scr, extra) {
  col(top); box([0, DESK_Y - 0.025, 0], [w, 0.05, d], MPL, T);
  col("#d8d1c4"); box([-w / 2 + 0.05, (DESK_Y - 0.05) / 2, 0], [0.04, DESK_Y - 0.05, d - 0.12], MPL, T); box([w / 2 - 0.05, (DESK_Y - 0.05) / 2, 0], [0.04, DESK_Y - 0.05, d - 0.12], MPL, T);
  box([0, 0.3, d / 2 - 0.08], [w - 0.12, 0.28, 0.02], MPL, T);
  var mx = extra && extra.mx !== undefined ? extra.mx : 0;
  col("#2a2e35"); box([mx, DESK_Y + 0.006, 0.2], [0.2, 0.012, 0.13], MPL, T); box([mx, DESK_Y + 0.1, 0.22], [0.045, 0.18, 0.03], MPL, T);
  box([mx, DESK_Y + 0.2, 0.195], [0.54, 0.32, 0.035], MPL, T);
  col(scr); box([mx, DESK_Y + 0.2, 0.176], [0.5, 0.28, 0.004], MSCR, T);
  if (extra && extra.dual) { col("#2a2e35"); box([mx + 0.58, DESK_Y + 0.2, 0.17], [0.5, 0.3, 0.035], MPL, TRm(T, rotY(-0.35))); col(scr); box([mx + 0.58, DESK_Y + 0.2, 0.151], [0.46, 0.26, 0.004], MSCR, TRm(T, rotY(-0.35))); }
  col("#30353d"); box([mx, DESK_Y + 0.008, -0.12], [0.34, 0.016, 0.11], MPL, T); box([mx + 0.25, DESK_Y + 0.01, -0.12], [0.05, 0.02, 0.08], MPL, T);
  col(rp(["#ffffff", "#f2c94c", "#ff8a5c", "#7fb7a4", "#c9b8ff", "#e9e4da"])); var mc = tf(T, [-w / 2 + 0.17, 0, -0.1]); cyl([mc[0], DESK_Y + 0.05, mc[2]], 0.035, 0.09, MPL, null, 10);
  col("#c9cdd1"); box([w / 2 - 0.2, DESK_Y + 0.01, -0.02], [0.3, 0.02, 0.24], MMET, T);
  if (extra && extra.plant) { var pc = tf(T, [-w / 2 + 0.16, 0, 0.2]); col("#efe9df"); cyl([pc[0], DESK_Y + 0.06, pc[2]], 0.05, 0.12, MPL, null, 10); col(rp(GREENS)); sphere([pc[0], DESK_Y + 0.17, pc[2]], 0.08, MLEAF, 8, 5, 1.2); }
  if (extra && extra.lamp) { var lc = tf(T, [-w / 2 + 0.12, 0, 0.22]); col("#3a4046"); cyl([lc[0], DESK_Y + 0.12, lc[2]], 0.012, 0.24, MPL, null, 6); col("#fff3d6"); cyl([lc[0], DESK_Y + 0.25, lc[2]], 0.06, 0.04, MLAMP, null, 10); }
}
function chair(T, fab, tall) {
  var i;
  col("#2b2f36");
  for (i = 0; i < 5; i++) { var a = i / 5 * TAU; box([0, 0.035, 0.12], [0.04, 0.03, 0.24], MPL, TRm(T, rotY(a))); }
  var c0 = tf(T, [0, 0, 0]); col("#9aa0a6"); cyl([c0[0], 0.15, c0[2]], 0.022, 0.2, MMET, null, 8);
  col(fab); box([0, SEAT_H - 0.035, 0.02], [0.4, 0.07, 0.4], MPL, T);
  box([0, SEAT_H + (tall ? 0.3 : 0.22), -0.19], [0.38, tall ? 0.52 : 0.36, 0.06], MPL, T);
  col("#2b2f36"); box([0, SEAT_H + 0.05, -0.17], [0.05, 0.12, 0.03], MPL, T);
  if (tall) { col(fab); box([0, SEAT_H + 0.6, -0.21], [0.3, 0.1, 0.07], MPL, T); }
}
function plant(x, z, s, kind, y0) {
  s = s || 1; y0 = y0 || 0;
  col(kind === 1 ? "#efe9df" : (kind === 2 ? "#3a4046" : "#c87a4f"));
  lathe([[0.0001, 0.4 * s], [0.19 * s, 0.4 * s], [0.15 * s, 0.0], [0.0001, 0.0]], 12, MPL, TT(I3, [x, y0, z]));
  col("#4b3a2a"); disk([x, y0 + 0.39 * s, z], 0.17 * s, MPL, 10);
  var g = rp(GREENS);
  if (kind === 2) {
    col(g); for (var i = 0; i < 7; i++) { var a = i / 7 * TAU + rn(0, 0.5), tl = rn(0.08, 0.22); lathe([[0.0001, (0.95 + rn(0, 0.35)) * s], [0.05 * s, 0.6 * s], [0.0001, 0.38 * s]], 6, MLEAF, TT(rotAxis(v3n([Math.cos(a), 0, Math.sin(a)]), tl), [x, y0, z])); }
    return;
  }
  col(g); sphere([x, y0 + 0.72 * s, z], 0.3 * s, MLEAF, 9, 6, 1.15);
  col(mix3(hex3(g), [1, 1, 0.8], 0.12)); sphere([x + 0.13 * s, y0 + 0.94 * s, z - 0.06 * s], 0.22 * s, MLEAF, 8, 5);
  col(mix3(hex3(g), [0, 0.1, 0], 0.15)); sphere([x - 0.12 * s, y0 + 0.88 * s, z + 0.09 * s], 0.2 * s, MLEAF, 8, 5);
}
/* 竖板（立在 x 处、面朝 +x）：看板 / 情绪板 / 招聘墙 */
function board3(x, z0, z1, acc, kind) {
  var L0 = z1 - z0, cz = (z0 + z1) / 2, c, r;
  col("#5d6268"); box([x, 0.5, z0 + 0.05], [0.05, 1.0, 0.05], MPL); box([x, 0.5, z1 - 0.05], [0.05, 1.0, 0.05], MPL);
  box([x, 0.05, z0 + 0.05], [0.4, 0.04, 0.06], MPL); box([x, 0.05, z1 - 0.05], [0.4, 0.04, 0.06], MPL);
  col(kind === 1 ? "#2f3138" : "#f7f4ee"); box([x, 1.18, cz], [0.04, 0.84, L0], MPL);
  col("#3a4046"); box([x, 1.61, cz], [0.06, 0.03, L0 + 0.04], MPL); box([x, 0.75, cz], [0.06, 0.03, L0 + 0.04], MPL);
  col(acc); box([x + 0.024, 1.53, cz], [0.006, 0.07, L0 - 0.08], MPL);
  var NC = kind === 1 ? ["#ff7eb6", "#ffb547", "#7fd3ff", "#a8e063", "#c9b8ff", "#ff8a5c", "#fbe7c6"] : ["#ffe08a", "#ffb3c7", "#a8e6cf", "#bde0fe", "#ffd6a5"];
  if (kind === 1) { for (r = 0; r < 3; r++) for (c = 0; c < 5; c++) { col(rp(NC)); box([x + 0.026, 1.36 - r * 0.2, z0 + 0.2 + c * (L0 - 0.4) / 4], [0.008, 0.16, (L0 - 0.4) / 5], MPL); } return; }
  for (c = 0; c < 3; c++) {
    var n0 = c === 0 ? 4 : (c === 1 ? 3 : 5);
    col("#c9c2b6"); if (c) box([x + 0.024, 1.14, z0 + c * L0 / 3], [0.006, 0.7, 0.012], MPL);
    for (r = 0; r < n0; r++) { col(rp(NC)); box([x + 0.026, 1.4 - r * 0.125, z0 + (c + 0.3 + (r % 2) * 0.38) * L0 / 3], [0.008, 0.095, 0.13], MPL); }
  }
}
function cabinetRow(x0, x1, z, h, c0) {
  var n = Math.max(1, Math.round((x1 - x0) / 0.55)), w = (x1 - x0) / n;
  for (var i = 0; i < n; i++) {
    var x = x0 + (i + 0.5) * w;
    col(c0 || "#e9e2d6"); box([x, h / 2, z], [w - 0.03, h, 0.42], MPL);
    col("#9aa0a6"); for (var y = 0.25; y < h - 0.1; y += 0.3) box([x, y, z + 0.215], [0.14, 0.02, 0.01], MMET);
  }
  col("#cfc6b6"); box([(x0 + x1) / 2, h + 0.01, z], [x1 - x0 + 0.02, 0.02, 0.44], MPL);
}
function shelf(x0, x1, z, h) {
  col("#7a5634"); box([(x0 + x1) / 2, h / 2, z], [x1 - x0, h, 0.32], MPL);
  col("#5a3e26"); for (var y = 0.35; y < h; y += 0.42) box([(x0 + x1) / 2, y, z + 0.02], [x1 - x0 - 0.06, 0.03, 0.3], MPL);
  var BK = ["#c8553d", "#3b4a63", "#e9dcc4", "#ffd36e", "#7fb7a4", "#2a2e35", "#b99bff", "#e0b48a"];
  for (var row = 0; row * 0.42 + 0.4 < h - 0.05; row++) for (var x = x0 + 0.08; x < x1 - 0.1; x += rn(0.07, 0.11)) { col(rp(BK)); box([x, 0.38 + row * 0.42 + 0.12, z + 0.04], [0.06, 0.24 + RND() * 0.06, 0.22], MPL); }
}
function roundTable(x, z, r, top) { col(top || "#f1ece3"); cyl([x, DESK_Y - 0.03, z], r, 0.06, MPL, null, 28); col("#9aa0a6"); cyl([x, 0.25, z], 0.05, 0.5, MMET, null, 10); cyl([x, 0.02, z], r * 0.45, 0.04, MPL, null, 18); }
function sofa(T, w, c0, c1) {
  col(c0); box([0, 0.2, 0], [w, 0.32, 0.72], MPL, T); box([0, 0.48, -0.3], [w, 0.62, 0.18], MPL, T); box([-w / 2 + 0.07, 0.4, 0], [0.14, 0.4, 0.72], MPL, T); box([w / 2 - 0.07, 0.4, 0], [0.14, 0.4, 0.72], MPL, T);
  col(c1); box([-w / 4 + 0.04, 0.38, 0.04], [w / 2 - 0.18, 0.06, 0.58], MPL, T); box([w / 4 - 0.04, 0.38, 0.04], [w / 2 - 0.18, 0.06, 0.58], MPL, T);
}
function beanbag(x, z, c0) { col(c0); sphere([x, 0.17, z], 0.36, MPL, 14, 7, 0.55); }

/* ---------- 墙：外墙（墙垛 + 玻璃 + 压顶）/ 内墙（下实上玻璃）/ 实墙；都只做轴对齐的段 ---------- */
function wallPiece(x0, z0, x1, z1, kind) {
  var L0 = hyp(x1 - x0, z1 - z0); if (L0 < 0.04) return;
  var T = TR(Math.atan2(-(z1 - z0), x1 - x0), (x0 + x1) / 2, (z0 + z1) / 2), s;
  if (kind === "ext") {
    col("#e6dccb"); box([0, 0.27, 0], [L0, 0.54, 0.32], MPL, T);
    col("#cfe6ee"); box([0, 0.95, 0], [L0, 0.8, 0.05], MGLASS, T);
    col("#f4ede2"); box([0, EXT_H - 0.03, 0], [L0 + 0.02, 0.06, 0.36], MPL, T);
    col("#b6977a"); box([0, EXT_H + 0.01, 0], [L0 + 0.02, 0.02, 0.37], MPL, T);
    col("#f4ede2"); for (s = -L0 / 2; s <= L0 / 2 + 0.01; s += Math.max(1.2, L0 / Math.max(1, Math.round(L0 / 2.4)))) box([clamp(s, -L0 / 2 + 0.17, L0 / 2 - 0.17), EXT_H / 2, 0], [0.34, EXT_H, 0.36], MPL, T);
  } else if (kind === "win") {
    col("#efe7da"); box([0, 0.24, 0], [L0, 0.48, 0.2], MPL, T);
    col("#d9eef3"); box([0, 0.76, 0], [L0, 0.56, 0.04], MGLASS, T);
    col("#c9a77c"); box([0, WALL_H, 0], [L0 + 0.02, 0.05, 0.24], MPL, T);
    col("#efe7da"); for (s = -L0 / 2; s <= L0 / 2 + 0.01; s += Math.max(1.0, L0 / Math.max(1, Math.round(L0 / 1.6)))) box([clamp(s, -L0 / 2 + 0.05, L0 / 2 - 0.05), 0.76, 0], [0.07, 0.56, 0.08], MPL, T);
  } else {
    col("#f3ece1"); box([0, WALL_H / 2, 0], [L0, WALL_H, 0.2], MPL, T);
    col("#c9a77c"); box([0, WALL_H, 0], [L0 + 0.02, 0.05, 0.24], MPL, T);
  }
}
/* 沿一条轴向线段，按门洞列表断开 */
function wallLine(x0, z0, x1, z1, gaps, kind) {
  var hor = Math.abs(z1 - z0) < 1e-6, a = hor ? x0 : z0, b = hor ? x1 : z1, cur = a;
  (gaps || []).slice().sort(function (p, q) { return p[0] - q[0]; }).forEach(function (g) {
    if (g[0] > cur) { if (hor) wallPiece(cur, z0, g[0], z0, kind); else wallPiece(x0, cur, x0, g[0], kind); }
    var pa = hor ? [g[0], z0] : [x0, g[0]], pb = hor ? [g[1], z0] : [x0, g[1]];
    col("#c9a77c"); box([pa[0], (kind === "ext" ? EXT_H : WALL_H) / 2, pa[1]], [0.16, kind === "ext" ? EXT_H : WALL_H, 0.16], MPL); box([pb[0], (kind === "ext" ? EXT_H : WALL_H) / 2, pb[1]], [0.16, kind === "ext" ? EXT_H : WALL_H, 0.16], MPL);
    cur = g[1];
  });
  if (b > cur) { if (hor) wallPiece(cur, z0, b, z0, kind); else wallPiece(x0, cur, x0, b, kind); }
}
/* =====================================================================
   9. 8 个部门房间（每个部门一份网格：地毯、工位、负责人桌、职能道具、门口立牌）
   ===================================================================== */
var DEPTM = [];
function furnish(k, R, D) {
  var hw = R.hw, hd = R.hd, ac = D.c, i, scr = mix3(D.col, [0.55, 0.85, 1.0], 0.55);
  /* 地毯 + 工位区彩色地垫 */
  col(mix3(D.col, [0.91, 0.87, 0.8], 0.8)); flat(-hw, -hd, hw, hd, 0.006, MCARPET);
  R.cl.forEach(function (c) {
    col(mix3(D.col, [0.92, 0.87, 0.8], 0.55)); flat(c[0] - 2.25, c[1] - 1.5, c[0] + 2.25, c[1] + 1.5, 0.014, MCARPET);
    for (i = 0; i < 6; i++) {
      var north = i < 3, u = c[0] + (i % 3 - 1) * 1.35, T = TR(north ? 0 : Math.PI, u, c[1] + (north ? -0.36 : 0.36));
      desk(T, 1.25, 0.7, "#efe8dc", scr, { plant: i === 2 || i === 3, lamp: i === 4 });
      chair(TR(north ? 0 : Math.PI, u, c[1] + (north ? -1.04 : 1.04)), "#3d4552", false);
    }
    col(mix3(D.col, [0.3, 0.3, 0.3], 0.25)); box([c[0], DESK_Y + 0.14, c[1]], [4.0, 0.26, 0.04], MPL);
    col("#e6dccb"); box([c[0], DESK_Y + 0.28, c[1]], [4.02, 0.025, 0.06], MPL);
  });
  /* 负责人桌（最里面，面朝房门） */
  var hu = R.head[0], hv = R.head[1];
  col(mix3(D.col, [0.92, 0.87, 0.8], 0.68)); flat(hu - 1.1, hv - 0.5, hu + 1.1, hv + 1.25, 0.014, MCARPET);
  var TH = TR(0, hu, hv + 0.6);
  desk(TH, 1.7, 0.78, "#d9bb92", scr, { dual: true, mx: -0.3, lamp: true });
  col(ac); box([0, DESK_Y + 0.05, 0.4], [0.5, 0.1, 0.02], MGLOW, TH);
  chair(TR(0, hu, hv - 0.04), "#2b2f36", true);
  /* 职能道具 */
  var Lw = -hw + 0.35;
  if (k === 0) { board3(Lw, -1.6, 1.4, ac, 0); col("#f7f4ee"); box([2.0, 1.1, -hd + 0.06], [2.6, 0.8, 0.04], MPL); for (i = 0; i < 14; i++) { col(rp(["#ffe08a", "#ffb3c7", "#a8e6cf", "#bde0fe", "#ffd6a5"])); box([0.85 + (i % 7) * 0.33, 1.3 - Math.floor(i / 7) * 0.32, -hd + 0.09], [0.22, 0.2, 0.01], MPL); } roundTable(2.3, -1.95, 0.32, "#ffd9a0"); }
  else if (k === 1) { board3(Lw, -1.6, 1.4, ac, 1); plant(hw - 0.5, -hd + 0.5, 1.25, 0); col("#2a2e35"); box([2.1, 1.05, -hd + 0.07], [1.6, 0.9, 0.05], MPL); col("#ffd1e3"); box([2.1, 1.05, -hd + 0.1], [1.48, 0.78, 0.005], MSCR); }
  else if (k === 2) { board3(Lw, -1.6, 1.4, ac, 0); [["#ff7a59", 0.9], ["#ffd166", 1.75], ["#7bdff2", 2.6]].forEach(function (q) { col("#2a2e35"); box([q[1], 1.05, -hd + 0.06], [0.7, 0.95, 0.04], MPL); col(q[0]); box([q[1], 1.05, -hd + 0.09], [0.6, 0.85, 0.01], MPL); });
    col("#2a2e35"); var tp = [2.35, -1.85]; [0, 2.1, 4.2].forEach(function (a) { tube([tp[0], 0, tp[1]], [tp[0] + 0.28 * Math.cos(a), 0.0, tp[1] + 0.28 * Math.sin(a)], 0.015, MPL, 4); tube([tp[0] + 0.28 * Math.cos(a), 0.0, tp[1] + 0.28 * Math.sin(a)], [tp[0], 0.95, tp[1]], 0.015, MPL, 4); }); box([tp[0], 1.0, tp[1]], [0.18, 0.12, 0.1], MPL); col("#fff3d6"); torus(0.22, 0.03, MLAMP, TT(rotX(Math.PI / 2), [tp[0] - 0.5, 1.2, tp[1]]), 18, 4); col("#3a4046"); tube([tp[0] - 0.5, 0, tp[1]], [tp[0] - 0.5, 0.98, tp[1]], 0.015, MPL, 4); }
  else if (k === 3) { board3(Lw, -1.6, 1.4, ac, 0); col("#22262d"); box([1.9, 1.08, -hd + 0.07], [3.4, 1.05, 0.08], MPL); for (i = 0; i < 2; i++) { col(i ? "#4fe0a6" : "#8fd8ff"); box([1.05 + i * 1.7, 1.08, -hd + 0.12], [1.6, 0.95, 0.005], MSCR); } plant(hw - 0.45, hd - 0.5, 0.95, 2); }
  else if (k === 4) { board3(Lw, -3.4, -0.2, ac, 0); col("#22262d"); for (i = 0; i < 2; i++) { box([3.65 + i * 0.0, 1.0, -hd + 0.45 + i * 0.66], [0.62, 2.0, 0.6], MPL); col("#5cc8ff"); box([3.33, 1.0, -hd + 0.45 + i * 0.66], [0.005, 1.8, 0.5], MSCR); col("#22262d"); } plant(-hw + 0.45, hd - 0.5, 1.05, 0); }
  else if (k === 5) { board3(Lw, -3.6, -0.6, ac, 0); roundTable(-3.1, 4.3, 0.42, "#f1ece3"); chair(TR(Math.PI / 2, -3.85, 4.3), "#b99bff", false); chair(TR(-Math.PI / 2, -2.35, 4.3), "#b99bff", false); sofa(TR(0, -3.0, -hd + 0.55), 1.6, "#8f7fbf", "#a99ad6"); plant(hw - 0.45, hd - 0.5, 1.0, 0); plant(-hw + 0.45, 1.0, 0.9, 2); }
  else if (k === 6) { board3(Lw, -1.6, 1.4, ac, 0); cabinetRow(0.6, 3.9, -hd + 0.25, 1.0, "#e2e6d6"); col("#3a3f47"); box([5.5, 0.45, -hd + 0.45], [0.66, 0.9, 0.6], MMET); col("#c9cdd1"); cyl([5.5, 0.5, -hd + 0.76], 0.12, 0.03, MMET, AXZ, 14); box([5.62, 0.5, -hd + 0.77], [0.05, 0.12, 0.02], MMET); plant(hw - 0.45, hd - 0.5, 0.95, 1); }
  else if (k === 7) { board3(Lw, -1.6, 1.4, ac, 0); shelf(0.5, 3.9, -hd + 0.2, 1.3); cabinetRow(5.0, 5.8, -hd + 0.25, 1.0, "#e9e2d6"); roundTable(2.6, 1.4, 0.38, "#d9bb92"); col("#c8553d"); cyl([2.55, DESK_Y + 0.05, 1.4], 0.05, 0.1, MPL, null, 10); col("#2a2e35"); box([2.75, DESK_Y + 0.01, 1.35], [0.22, 0.02, 0.3], MPL); plant(hw - 0.45, hd - 0.5, 0.95, 0); }
  /* 门口立牌 + 塔灯（在房门里侧） */
  var tu = R.doorU - 0.85, tv = hd - 0.32;
  col("#3a4046"); box([tu, 0.75, tv], [0.08, 1.5, 0.08], MPL); box([tu, 0.03, tv], [0.34, 0.06, 0.34], MPL);
  col(ac); box([tu, 1.22, tv], [0.56, 0.34, 0.06], MPL);
  col("#fff6e6"); box([tu, 1.22, tv + 0.035], [0.46, 0.06, 0.012], MLEDV);
  andon(tu, 1.39, tv);
  /* 门外墙上的门牌（朝走廊） */
  col(ac); box([R.doorU + 1.05, 0.72, hd + 0.14], [0.8, 0.26, 0.04], MPL); col("#fff6e6"); box([R.doorU + 1.05, 0.72, hd + 0.165], [0.6, 0.05, 0.01], MLEDV);
}
DEPTS.forEach(function (D, k) {
  var g = begin(); GT = D.R.T; furnish(k, D.R, D); GT = null; DEPTM.push(g);
});

/* =====================================================================
   10. 楼体：台基、走廊地板、墙、总经理办公室（Chief）、大堂（会议室 / 前台 / 茶水间）
   ===================================================================== */
var OFFICE = begin(), ROOMM = {};
(function buildOffice() {
  var i, x, z;
  /* 沙盘底座（深色 + 金色描边）+ 台基 + 入口台阶 */
  col("#262a31"); box([0, GY - 0.36, 0.35], [BX1 - BX0 + 2.6, 0.72, BZ1 - BZ0 + 3.4], MPL);
  col("#1c1f25"); box([0, GY - 0.75, 0.35], [BX1 - BX0 + 2.2, 0.1, BZ1 - BZ0 + 3.0], MPL);
  col("#c9a34a"); var bw = BX1 - BX0 + 2.6, bd = BZ1 - BZ0 + 3.4;
  box([0, GY + 0.004, 0.35 - bd / 2 + 0.03], [bw, 0.012, 0.06], MLEDV); box([0, GY + 0.004, 0.35 + bd / 2 - 0.03], [bw, 0.012, 0.06], MLEDV);
  box([-bw / 2 + 0.03, GY + 0.004, 0.35], [0.06, 0.012, bd], MLEDV); box([bw / 2 - 0.03, GY + 0.004, 0.35], [0.06, 0.012, bd], MLEDV);
  col("#d8cfc1"); box([0, GY / 2 - 0.003, 0], [BX1 - BX0 + 0.7, -GY, BZ1 - BZ0 + 0.7], MPL);
  col("#cfc5b5"); box([0, GY * 0.75, BZ1 + 0.75], [3.4, -GY / 2, 0.8], MPL); box([0, GY * 0.25 - 0.003, BZ1 + 0.5], [3.0, -GY / 2, 0.5], MPL);
  /* 走廊地板（浅橡木），大堂、总经理办公室单独铺 */
  col("#cfae86"); flat(BX0, BZ0, BX1, BZ1, 0, MWOOD);
  col("#ece3d3"); flat(-6, 6.2, 6, BZ1, 0.004, MFLOOR);
  col("#b98a5e"); flat(-6, 6.2, -2.2, BZ1, 0.006, MWOOD);
  col("#a9c58a"); flat(2.4, 9.3, 5.8, 11.4, 0.01, MCARPET);
  /* ---- 中央：总经理办公室（Chief 的办公室）----
     深色木地板 + 地毯；北墙两侧书柜、中间矮柜和大屏；大班台 + 老板椅 + 两把访客椅；西侧沙发茶台；东侧 6 人会议桌 */
  function execChair(T, c0, c1) {
    var k; col("#1f1f22"); for (k = 0; k < 5; k++) box([0, 0.04, 0.15], [0.05, 0.035, 0.3], MPL, TRm(T, rotY(k / 5 * TAU)));
    var c = tf(T, [0, 0, 0]); col("#9aa0a6"); cyl([c[0], 0.17, c[2]], 0.03, 0.22, MMET, null, 8);
    col(c0); box([0, SEAT_H - 0.04, 0.03], [0.6, 0.12, 0.56], MPL, T); box([0, SEAT_H + 0.42, -0.25], [0.58, 0.8, 0.12], MPL, T);
    col(c1); box([0, SEAT_H + 0.86, -0.27], [0.46, 0.14, 0.14], MPL, T); box([-0.31, SEAT_H + 0.16, 0.0], [0.07, 0.06, 0.42], MPL, T); box([0.31, SEAT_H + 0.16, 0.0], [0.07, 0.06, 0.42], MPL, T);
    col("#1f1f22"); box([-0.31, SEAT_H + 0.06, 0.05], [0.04, 0.14, 0.05], MPL, T); box([0.31, SEAT_H + 0.06, 0.05], [0.04, 0.14, 0.05], MPL, T);
  }
  function armchair(T, c0, c1) {
    col(c0); box([0, 0.2, 0], [0.7, 0.3, 0.66], MPL, T); box([0, 0.5, -0.28], [0.7, 0.5, 0.14], MPL, T);
    box([-0.31, 0.38, 0], [0.12, 0.26, 0.66], MPL, T); box([0.31, 0.38, 0], [0.12, 0.26, 0.66], MPL, T);
    col(c1); box([0, 0.37, 0.04], [0.48, 0.06, 0.52], MPL, T);
    col("#1f1f22"); [[-0.3, -0.28], [0.3, -0.28], [-0.3, 0.28], [0.3, 0.28]].forEach(function (q) { box([q[0], 0.03, q[1]], [0.05, 0.06, 0.05], MPL, T); });
  }
  col("#7a5236"); flat(GM.x0, GM.z0, GM.x1, GM.z1, 0.004, MWOOD);
  col("#4e3a2e"); flat(-2.7, -3.75, 2.7, -0.15, 0.012, MCARPET);
  col("#b8964a"); [[-2.7, -3.75, 2.7, -3.67], [-2.7, -0.23, 2.7, -0.15], [-2.7, -3.75, -2.62, -0.15], [2.62, -3.75, 2.7, -0.15]].forEach(function (q) { flat(q[0], q[1], q[2], q[3], 0.016, MCARPET); });
  col("#6e6255"); flat(-5.3, -0.95, -1.75, 3.65, 0.012, MCARPET);
  /* 北墙：两侧书柜 + 中间矮柜和大屏（大屏上画各项目进度柱） */
  shelf(-5.35, -2.05, -4.13, 1.5); shelf(2.05, 5.35, -4.13, 1.5);
  col("#3e2b1f"); box([0, 0.3, -4.02], [3.7, 0.6, 0.5], MPL); col("#2a1d15"); box([0, 0.615, -4.02], [3.8, 0.03, 0.56], MPL);
  col("#c9a34a"); box([-0.9, 0.3, -3.765], [0.5, 0.02, 0.01], MMET); box([0.9, 0.3, -3.765], [0.5, 0.02, 0.01], MMET);
  col("#15181d"); box([0, 1.13, GM_SCR[1] - 0.04], [3.1, 1.0, 0.06], MPL);
  col("#0f2433"); box([0, 1.13, GM_SCR[1]], [2.96, 0.88, 0.006], MSCR);
  /* 大班台（Chief 坐北朝南）*/
  var DZ = HOLO[1], DY = 0.6;
  col("#5a3a24"); box([0, DY - 0.03, DZ], [2.7, 0.06, 1.1], MWOOD);
  col("#2c2420"); box([0, DY + 0.002, DZ - 0.08], [1.5, 0.006, 0.55], MPL);
  col("#4a2f1d"); box([-1.02, (DY - 0.06) / 2, DZ], [0.6, DY - 0.06, 1.0], MPL); box([1.02, (DY - 0.06) / 2, DZ], [0.6, DY - 0.06, 1.0], MPL); box([0, 0.34, DZ + 0.5], [1.5, 0.44, 0.04], MPL);
  col("#c9a34a"); [-1.02, 1.02].forEach(function (px) { for (var r = 0; r < 3; r++) box([px, 0.12 + r * 0.16, DZ - 0.51], [0.22, 0.02, 0.012], MMET); });
  col("#22252b"); box([-0.45, DY + 0.006, DZ - 0.22], [0.24, 0.012, 0.16], MPL); box([-0.45, DY + 0.12, DZ - 0.2], [0.04, 0.22, 0.03], MPL); box([-0.45, DY + 0.27, DZ - 0.17], [0.66, 0.38, 0.03], MPL);
  col("#9fd8ff"); box([-0.45, DY + 0.27, DZ - 0.19], [0.62, 0.34, 0.004], MSCR);
  col("#c9a34a"); cyl([0.95, DY + 0.015, DZ - 0.3], 0.07, 0.03, MMET, null, 12); tube([0.95, DY + 0.02, DZ - 0.3], [0.95, DY + 0.3, DZ - 0.3], 0.012, MMET, 6);
  col("#2f7d55"); box([0.95, DY + 0.33, DZ - 0.24], [0.34, 0.07, 0.14], MPL); col("#fff3d6"); box([0.95, DY + 0.293, DZ - 0.24], [0.3, 0.006, 0.1], MLAMP);
  col("#2a2e35"); cyl([-1.05, DY + 0.02, DZ + 0.32], 0.08, 0.04, MPL, null, 12);
  col("#c9a34a"); tube([-1.12, DY + 0.03, DZ + 0.32], [-1.12, DY + 0.42, DZ + 0.32], 0.008, MMET, 5); tube([-0.98, DY + 0.03, DZ + 0.32], [-0.98, DY + 0.42, DZ + 0.32], 0.008, MMET, 5);
  col("#ffc46b"); box([-1.19, DY + 0.36, DZ + 0.32], [0.14, 0.1, 0.006], MPL); col("#2f6db5"); box([-0.91, DY + 0.36, DZ + 0.32], [0.14, 0.1, 0.006], MPL);
  col("#2a2e35"); box([0.15, DY + 0.04, DZ + 0.45], [0.46, 0.08, 0.05], MPL); col("#ffc46b"); box([0.15, DY + 0.04, DZ + 0.477], [0.36, 0.025, 0.004], MLEDV);
  col("#f4efe6"); cyl([0.4, DY + 0.045, DZ - 0.35], 0.045, 0.09, MPL, null, 10); col("#2a2e35"); cyl([-1.0, DY + 0.06, DZ - 0.3], 0.04, 0.12, MPL, null, 8);
  col("#c9cdd1"); box([0.6, DY + 0.01, DZ + 0.1], [0.3, 0.02, 0.24], MMET);
  execChair(TR(0, 0, -3.0), "#2b1d16", "#3a281e");
  armchair(TR(Math.PI, -0.85, -0.95), "#6b4a34", "#7d5a42"); armchair(TR(Math.PI, 0.85, -0.95), "#6b4a34", "#7d5a42");
  /* 西侧：沙发 + 两把单人椅 + 茶台 */
  sofa(TR(Math.PI / 2, -4.85, 1.35), 2.4, "#3d2b20", "#55402f");
  armchair(TR(0, -3.25, -0.4), "#6b4a34", "#7d5a42"); armchair(TR(Math.PI, -3.25, 3.1), "#6b4a34", "#7d5a42");
  col("#4a2f1d"); box([-3.25, 0.36, 1.35], [0.8, 0.08, 1.7], MWOOD); box([-3.25, 0.17, 0.67], [0.7, 0.32, 0.1], MPL); box([-3.25, 0.17, 2.03], [0.7, 0.32, 0.1], MPL);
  col("#2a1d15"); box([-3.25, 0.41, 1.35], [0.5, 0.025, 0.7], MPL);
  col("#7a4a2a"); sphere([-3.25, 0.47, 1.17], 0.065, MPL, 10, 6, 0.85); col("#f4efe6"); [[-3.4, 1.5], [-3.1, 1.5], [-3.4, 1.66], [-3.1, 1.66]].forEach(function (q) { cyl([q[0], 0.445, q[1]], 0.025, 0.04, MPL, null, 8); });
  col("#c9cdd1"); cyl([-3.0, 0.48, 0.8], 0.06, 0.14, MMET, null, 10);
  col("#2a2e35"); cyl([-5.15, 0.02, -0.6], 0.15, 0.04, MPL, null, 12); tube([-5.15, 0.02, -0.6], [-5.15, 1.45, -0.6], 0.015, MPL, 6); col("#f4ead6"); lathe([[0.0001, 1.62], [0.12, 1.62], [0.18, 1.4], [0.0001, 1.4]], 12, MLAMP, TT(I3, [-5.15, 0, -0.6]));
  /* 东侧：6 人会议桌（Chief 召集负责人周会就在这里）*/
  col("#5a3a24"); box([GM_TBL[0], DESK_Y - 0.03, GM_TBL[1]], [1.1, 0.06, 2.6], MWOOD); col("#4a2f1d"); box([GM_TBL[0], 0.25, GM_TBL[1] - 0.9], [0.7, 0.5, 0.1], MPL); box([GM_TBL[0], 0.25, GM_TBL[1] + 0.9], [0.7, 0.5, 0.1], MPL);
  [-0.8, 0, 0.8].forEach(function (dz) {
    chair(TR(Math.PI / 2, GM_TBL[0] - 0.85, GM_TBL[1] + dz), "#2b1d16", true); chair(TR(-Math.PI / 2, GM_TBL[0] + 0.85, GM_TBL[1] + dz), "#2b1d16", true);
    col("#f7f4ee"); box([GM_TBL[0] - 0.3, DESK_Y + 0.003, GM_TBL[1] + dz], [0.22, 0.004, 0.3], MPL); box([GM_TBL[0] + 0.3, DESK_Y + 0.003, GM_TBL[1] + dz], [0.22, 0.004, 0.3], MPL);
  });
  col("#cfe6ee"); [-1.1, 1.1].forEach(function (dz) { cyl([GM_TBL[0], DESK_Y + 0.08, GM_TBL[1] + dz], 0.035, 0.16, MGLASS, null, 8); });
  /* 绿植 + 门外铭牌 */
  plant(-5.15, 3.95, 1.35, 2); plant(5.15, 3.95, 1.35, 2); plant(-1.3, 3.95, 0.95, 1); plant(1.3, 3.95, 0.95, 1); plant(5.15, -0.4, 1.15, 0);
  col("#2a2e35"); box([1.45, 0.8, GM.z1 + 0.13], [0.9, 0.26, 0.03], MPL); col("#c9a34a"); box([1.45, 0.8, GM.z1 + 0.147], [0.78, 0.05, 0.006], MLEDV);
  /* ---- 墙 ---- */
  wallLine(BX0, BZ0, BX1, BZ0, [], "ext");
  wallLine(BX0, BZ1, BX1, BZ1, [[-1.3, 1.3]], "ext");
  wallLine(BX0, BZ0, BX0, BZ1, [], "ext");
  wallLine(BX1, BZ0, BX1, BZ1, [], "ext");
  /* 总经理办公室：北面实墙，其余三面玻璃隔断，南面开门 */
  wallLine(GM.x0, GM.z0, GM.x1, GM.z0, [], "solid"); wallLine(GM.x0, GM.z1, GM.x1, GM.z1, [[-0.65, 0.65]], "win");
  wallLine(GM.x0, GM.z0, GM.x0, GM.z1, [], "win"); wallLine(GM.x1, GM.z0, GM.x1, GM.z1, [], "win");
  /* 北边 4 个房间：南墙带门，房间之间实墙 */
  var ng = []; ["N1", "N2", "N3", "N4"].forEach(function (k) { var R = ROOMS[k], dx = R.cx + R.doorU; ng.push([dx - 0.6, dx + 0.6]); });
  wallLine(BX0, -6.2, BX1, -6.2, ng, "win");
  [-9, 0, 9].forEach(function (xx) { wallLine(xx, BZ0, xx, -6.2, [], "solid"); });
  /* 西 / 东大房间 */
  wallLine(BX0, -4.4, -7.4, -4.4, [], "win"); wallLine(BX0, 4.4, -7.4, 4.4, [], "win"); wallLine(-7.4, -4.4, -7.4, 4.4, [[-3.7, -2.5]], "win");
  wallLine(7.4, -4.4, BX1, -4.4, [], "win"); wallLine(7.4, 4.4, BX1, 4.4, [], "win"); wallLine(7.4, -4.4, 7.4, 4.4, [[2.5, 3.7]], "win");
  /* 南边：两个部门房间 + 大堂（会议室有门，前台、茶水间敞开） */
  wallLine(BX0, 6.2, -6, 6.2, [[-17.3, -16.1]], "win"); wallLine(6, 6.2, BX1, 6.2, [[6.7, 7.9]], "win");
  wallLine(-6, 6.2, -6, BZ1, [], "solid"); wallLine(6, 6.2, 6, BZ1, [], "solid");
  wallLine(-6, 6.2, -2.2, 6.2, [[-3.6, -2.6]], "win"); wallLine(-2.2, 6.2, -2.2, BZ1, [], "win");
  /* ---- 大堂：会议室（长桌 6 座）---- */
  var meet = begin();
  col("#f1ece3"); box([MEET_C[0], DESK_Y - 0.03, MEET_C[1]], [1.0, 0.06, 2.7], MPL);
  col("#ffffff"); box([MEET_C[0], DESK_Y - 0.065, MEET_C[1]], [0.85, 0.012, 2.5], MGLOW);
  col("#c9c2b6"); box([MEET_C[0], 0.25, MEET_C[1] - 1.1], [0.8, 0.5, 0.1], MPL); box([MEET_C[0], 0.25, MEET_C[1] + 1.1], [0.8, 0.5, 0.1], MPL);
  [8.5, 9.35, 10.2].forEach(function (zz) { chair(TR(Math.PI / 2, -4.95, zz), "#c8553d", false); chair(TR(-Math.PI / 2, -3.25, zz), "#c8553d", false); });
  col("#2a2e35"); box([MEET_C[0], 1.05, 12.2], [1.7, 0.9, 0.06], MPL); col("#ffe6b0"); box([MEET_C[0], 1.05, 12.16], [1.6, 0.8, 0.005], MSCR);
  ROOMM.meet = meet; CM = OFFICE;
  plant(-5.6, 6.65, 0.85, 2);
  /* 前台：接待台 + 会议室玻璃墙上的 HQ 字 */
  col("#f2ede4"); box([-1.25, 0.45, 9.6], [0.7, 0.9, 2.0], MPL); col("#a0703f"); box([-1.25, 0.92, 9.6], [0.82, 0.05, 2.1], MPL); col("#ffc46b"); box([-0.89, 0.5, 9.6], [0.012, 0.06, 1.8], MLEDV);
  col("#2a2e35"); box([-1.4, 1.1, 9.3], [0.03, 0.24, 0.38], MPL);
  col("#ffc46b"); var hz = 11.25, hy = 0.78;
  box([-2.1, hy, hz + 0.22], [0.08, 0.5, 0.08], MLEDV); box([-2.1, hy, hz - 0.08], [0.08, 0.5, 0.08], MLEDV); box([-2.1, hy, hz + 0.07], [0.08, 0.07, 0.24], MLEDV);
  var qz = 10.65; box([-2.1, hy + 0.21, qz], [0.08, 0.07, 0.32], MLEDV); box([-2.1, hy - 0.21, qz], [0.08, 0.07, 0.32], MLEDV); box([-2.1, hy, qz - 0.13], [0.08, 0.5, 0.07], MLEDV); box([-2.1, hy, qz + 0.13], [0.08, 0.5, 0.07], MLEDV);
  plant(1.6, 12.0, 1.05, 0); plant(-1.7, 12.0, 0.9, 1);
  /* 茶水间：吧台 + 咖啡机 + 冰箱 + 沙发 + 懒人沙发 */
  col("#f2ede4"); box([5.65, 0.45, 7.9], [0.6, 0.9, 2.2], MPL); col("#a0703f"); box([5.62, 0.92, 7.9], [0.7, 0.05, 2.3], MPL);
  col("#2a2e35"); box([5.75, 1.12, 7.4], [0.36, 0.36, 0.34], MPL); col("#ff8a5c"); box([5.56, 1.2, 7.4], [0.005, 0.06, 0.16], MLEDV);
  col("#2a2e35"); box([5.75, 1.1, 8.4], [0.34, 0.32, 0.3], MPL); col("#ffb27a"); box([5.57, 1.18, 8.4], [0.005, 0.05, 0.14], MLEDV);
  for (i = 0; i < 4; i++) { col(rp(["#ffffff", "#ff8a5c", "#ffd36e", "#7fb7a4"])); cyl([5.55, 0.99, 8.8 + i * 0.1 - 0.15], 0.035, 0.09, MPL, null, 8); }
  col("#dfe3e6"); box([5.65, 0.95, 9.55], [0.6, 1.9, 0.62], MPL); col("#9aa0a6"); box([5.34, 1.3, 9.4], [0.02, 0.5, 0.03], MMET);
  sofa(TR(Math.PI, 3.8, 11.95), 2.2, "#5b8a72", "#6fa088");
  col("#a0703f"); cyl([3.8, 0.32, 10.85], 0.32, 0.04, MPL, null, 20); col("#3a4046"); cyl([3.8, 0.16, 10.85], 0.03, 0.3, MPL, null, 8);
  beanbag(3.0, 9.9, "#ff8a3d"); beanbag(4.6, 10.0, "#ffb547");
  plant(5.6, 11.95, 1.0, 0); plant(2.6, 6.65, 0.85, 2);
  /* 走廊尽头 + 拐角的绿植 */
  [[-17.5, -5.3], [17.5, -5.3], [-17.5, 5.3], [17.5, 5.3]].forEach(function (q, j) { plant(q[0], q[1] + (q[1] < 0 ? -0.55 : 0.55), 0.9, j % 3); });
})();

/* =====================================================================
   12. 人（批量绘制模板）：Q 版大头；衣服 = 胶带色槽、头发 = 第二色槽、Agent 光环 / 胸牌 = 自发光色槽
       Chief 额外有一圈金色王冠光环
   ===================================================================== */
var PH = { head: [], hair: [], headMap: {} }, DOCT = null, HALO = null, CROWN = null;
(function buildPeople() {
  SKINS.forEach(function (sk) {
    var g = begin();
    col(sk); sphere([0, 0, 0], 0.19, MPL, 12, 7, 1.0);
    col("#2a2018"); box([-0.066, -0.005, 0.176], [0.034, 0.05, 0.02], MPL); box([0.066, -0.005, 0.176], [0.034, 0.05, 0.02], MPL);
    col("#ffffff"); box([-0.06, 0.008, 0.187], [0.01, 0.012, 0.004], MPL); box([0.072, 0.008, 0.187], [0.01, 0.012, 0.004], MPL);
    col("#ff9e94"); box([-0.11, -0.06, 0.15], [0.05, 0.022, 0.01], MPL); box([0.11, -0.06, 0.15], [0.05, 0.022, 0.01], MPL);
    col("#b35a4a"); box([0, -0.085, 0.177], [0.04, 0.012, 0.01], MPL);
    g.keep = true; PH.head.push(g); PH.headMap[sk] = PH.head.length - 1;
  });
  function cap() {
    var prof = [], nrm = [], i, sv = 6, r = 0.206;
    for (i = 0; i <= sv; i++) { var a = Math.PI / 2 - i / sv * Math.PI / 2 * 1.05; prof.push([Math.max(0.0001, r * Math.cos(a)), r * Math.sin(a)]); nrm.push([Math.cos(a), Math.sin(a)]); }
    latheS(prof, nrm, 12, MSLOT2, TT(rotX(-0.32), [0, 0.035, -0.012]));
    sphere([0, -0.02, -0.06], 0.192, MSLOT2, 10, 6);
  }
  var g;
  g = begin(); col("#ffffff"); cap(); PH.hair.push(g);
  g = begin(); col("#ffffff"); cap(); sphere([0, -0.07, -0.05], 0.2, MSLOT2, 10, 6, 1.0); sphere([0.15, -0.07, 0.0], 0.09, MSLOT2, 8, 5, 1.4); sphere([-0.15, -0.07, 0.0], 0.09, MSLOT2, 8, 5, 1.4); PH.hair.push(g);
  g = begin(); col("#ffffff"); cap(); [[0, 0.21, 0.02, 0.0], [0.09, 0.19, -0.02, -0.5], [-0.09, 0.19, -0.02, 0.5], [0.04, 0.17, -0.12, 0.3], [-0.05, 0.17, -0.12, -0.3]].forEach(function (q) { lathe([[0.0001, 0.11], [0.05, 0.0], [0.0001, 0.0]], 6, MSLOT2, TT(m3mul(rotZ(q[3]), rotX(-0.35)), [q[0], q[1], q[2]])); }); PH.hair.push(g);
  g = begin(); col("#ffffff"); cap(); box([0, -0.2, -0.13], [0.34, 0.34, 0.1], MSLOT2); box([0.165, -0.12, -0.02], [0.05, 0.26, 0.16], MSLOT2); box([-0.165, -0.12, -0.02], [0.05, 0.26, 0.16], MSLOT2); PH.hair.push(g);
  g = begin(); col("#ffffff"); cap(); sphere([0, 0.235, -0.07], 0.095, MSLOT2, 10, 6); PH.hair.push(g);
  PH.hair.forEach(function (h) { h.keep = true; });
  function torso(ai) {
    var g0 = begin();
    col("#ffffff"); lathe([[0.0001, 0.33], [0.1, 0.325], [0.165, 0.29], [0.175, 0.17], [0.165, 0.05], [0.15, 0.0], [0.0001, 0.0]], 12, MTAPE, TT([1, 0, 0, 0, 1, 0, 0, 0, 0.74], [0, 0, 0]));
    col("#3a4250"); lathe([[0.0001, 0.06], [0.152, 0.06], [0.155, -0.04], [0.0001, -0.04]], 12, MPL, TT([1, 0, 0, 0, 1, 0, 0, 0, 0.74], [0, 0, 0]));
    if (ai) { col("#ffffff"); box([0, 0.19, 0.123], [0.13, 0.08, 0.012], MEMIT); col("#2a2e35"); box([0, 0.19, 0.118], [0.15, 0.1, 0.008], MPL); }
    else { col("#ffffff"); box([0.075, 0.17, 0.124], [0.07, 0.09, 0.01], MGLOW); col("#2a2e35"); tube([0.02, 0.31, 0.1], [0.075, 0.22, 0.125], 0.006, MPL, 4); }
    g0.keep = true; return g0;
  }
  PH.torsoA = torso(true); PH.torsoH = torso(false);
  PH.arm = begin(); col("#ffffff"); box([0, -0.1, 0], [0.085, 0.2, 0.085], MTAPE); col("#f0c8a0"); box([0, -0.225, 0], [0.07, 0.06, 0.07], MPL); PH.arm.keep = true;
  PH.thigh = begin(); col("#3a4250"); box([0, -0.1, 0], [0.105, 0.22, 0.11], MPL); PH.thigh.keep = true;
  PH.shin = begin(); col("#3a4250"); box([0, -0.1, 0], [0.095, 0.2, 0.1], MPL); col("#1f2228"); box([0, -0.215, 0.03], [0.1, 0.05, 0.17], MPL); PH.shin.keep = true;
  HALO = begin(); col("#ffffff"); torus(0.135, 0.017, MEMIT, null, 22, 4); HALO.keep = true;
  CROWN = begin(); col("#ffffff"); torus(0.2, 0.022, MGLOW, null, 28, 4); for (var i = 0; i < 6; i++) { var a = i / 6 * TAU; lathe([[0.0001, 0.12], [0.03, 0.0], [0.0001, 0.0]], 6, MGLOW, TT(I3, [Math.cos(a) * 0.2, 0.0, Math.sin(a) * 0.2])); } CROWN.keep = true;
  DOCT = begin();
  col("#ffffff"); box([0, 0.018, 0], [0.26, 0.032, 0.2], MTAPE);
  col("#f7f4ee"); box([0.01, 0.036, 0.008], [0.22, 0.006, 0.17], MPL);
  col("#ffffff"); box([0.09, 0.041, -0.05], [0.05, 0.004, 0.05], MSLOT2);
  DOCT.keep = true;
})();

var GEO_ALL = [SKY, OFFICE, ROOMM.meet].concat(DEPTM);

/* =====================================================================
   13. WebGL：阴影、昼夜光照、材质、特效层、移轴 + 泛光后期
      优先用 WebGL2（多重采样抗锯齿），不行就退回 WebGL1；着色器统一用 GLSL ES 1.0
   ===================================================================== */
var gl = null, GL2 = false;
try { gl = glc.getContext("webgl2", { antialias: false, alpha: false, depth: true, premultipliedAlpha: false, powerPreference: "high-performance" }); GL2 = !!gl; } catch (e) { gl = null; }
if (!gl) { try { gl = glc.getContext("webgl", { antialias: false, alpha: false, depth: true, premultipliedAlpha: false, powerPreference: "high-performance" }); } catch (e2) { gl = null; } }
if (!gl) $("glFail").hidden = false;
var HP = "#ifdef GL_FRAGMENT_PRECISION_HIGH\nprecision highp float;\n#else\nprecision mediump float;\n#endif\n";

var VS_MAIN = [
  "precision highp float;",
  "attribute vec3 aP; attribute vec3 aN; attribute vec3 aC; attribute float aM; attribute float aU;",
  "uniform mat4 uVP; uniform mat4 uModel;",
  "varying vec3 vW; varying vec3 vN; varying vec3 vC; varying float vM; varying float vU;",
  "void main(){ vec4 w = uModel * vec4(aP, 1.0); vW = w.xyz; vN = (uModel * vec4(aN, 0.0)).xyz; vC = aC; vM = aM; vU = aU; gl_Position = uVP * w; }"
].join("\n");
var FS_MAIN = HP + [
  "varying vec3 vW; varying vec3 vN; varying vec3 vC; varying float vM; varying float vU;",
  "uniform vec3 uCam; uniform float uTime, uBelt, uAct, uAndon, uNight, uWin, uExp;",
  "uniform vec3 uAcc, uPaint; uniform float uCloud;",
  "uniform vec3 uSunDir, uSunC, uSkyC, uGndC, uFogC, uSkyTop, uHorz;",
  "uniform float uFogD, uFogS; uniform vec3 uBgT, uBgB; uniform vec2 uRes;",
  "uniform mat4 uSM; uniform sampler2D uShadow; uniform float uShOn, uShB, uShN; uniform vec2 uShPx;",
  "uniform vec3 uLP[8]; uniform vec3 uLC[8];",
  "float h2(vec2 p){ return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }",
  "float vn(vec2 p){ vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f); return mix(mix(h2(i), h2(i + vec2(1.0, 0.0)), f.x), mix(h2(i + vec2(0.0, 1.0)), h2(i + vec2(1.0, 1.0)), f.x), f.y); }",
  "float unpack(vec4 v){ return dot(v, vec4(1.0, 1.0 / 255.0, 1.0 / 65025.0, 1.0 / 16581375.0)); }",
  "float shadowAt(vec3 p, vec3 n){",
  "  if (uShOn < 0.5) return 1.0;",
  "  vec4 q = uSM * vec4(p + n * uShN, 1.0); vec3 s = q.xyz / q.w * 0.5 + 0.5;",
  "  if (s.x < 0.002 || s.x > 0.998 || s.y < 0.002 || s.y > 0.998 || s.z > 0.999) return 1.0;",
  "  float sum = 0.0;",
  "  for (int i = -1; i <= 1; i++) for (int j = -1; j <= 1; j++) { float d = unpack(texture2D(uShadow, s.xy + vec2(float(i), float(j)) * uShPx * 1.6)); sum += step(s.z - uShB, d); }",
  "  return sum / 9.0;",
  "}",
  "vec3 lightIt(vec3 p, vec3 n, vec3 alb, float gs, float sh, float sd){",
  "  vec3 v = normalize(uCam - p);",
  "  float hemi = 0.5 + 0.5 * n.y;",
  "  vec3 col = alb * mix(uGndC, uSkyC, hemi);",
  "  float ndl = max(dot(n, uSunDir), 0.0);",
  "  col += alb * uSunC * ndl * sd;",
  "  col += uSunC * pow(max(dot(n, normalize(uSunDir + v)), 0.0), sh) * gs * sd * 0.6;",
  "  for (int i = 0; i < 8; i++) {",
  "    vec3 d = uLP[i] - p; float d2 = dot(d, d); vec3 L = d * inversesqrt(d2 + 0.0001);",
  "    float att = 1.0 / (1.0 + d2 * 0.35);",
  "    col += (alb * max(dot(n, L), 0.0) + pow(max(dot(n, normalize(L + v)), 0.0), sh) * gs * 0.5) * uLC[i] * att;",
  "  }",
  "  return col;",
  "}",
  "vec3 aces(vec3 x){ return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0); }",
  "void main(){",
  "  vec3 n = normalize(vN); if (!gl_FrontFacing) n = -n;",
  "  float m = vM; vec3 alb = vC; float gs = 0.12, sh = 18.0; vec3 em = vec3(0.0); bool lit = true; float dist = length(uCam - vW);",
  "  vec3 vd = normalize(vW - uCam);",
  "  if (m < 0.5) { gs = 0.12; }",
  "  else if (m < 1.5) {",
  "    vec2 p = vW.xz; float fd = clamp(1.0 - dist / 120.0, 0.0, 1.0);",
  "    float n1 = vn(p * 0.18), n2 = vn(p * 1.3), n3 = h2(floor(p * 9.0));",
  "    alb *= 0.82 + 0.22 * n1 + (0.1 * n2 + 0.08 * n3 - 0.09) * fd;",
  "    alb *= 1.0 - 0.06 * step(0.5, fract(p.x * 0.25 + p.y * 0.05)) * fd;",
  "    vec2 c = floor(p * 2.6); float r = h2(c + 7.1); vec2 f = fract(p * 2.6) - 0.5 - (vec2(h2(c + 1.3), h2(c + 2.7)) - 0.5) * 0.5;",
  "    float dot1 = (1.0 - smoothstep(0.07, 0.12, length(f))) * step(0.9, r) * fd * (1.0 - step(0.5, vU));",
  "    vec3 fc = r > 0.975 ? vec3(0.95, 0.92, 0.85) : (r > 0.95 ? vec3(0.95, 0.78, 0.25) : (r > 0.925 ? vec3(0.62, 0.48, 0.86) : vec3(0.92, 0.55, 0.7)));",
  "    alb = mix(alb, fc * fc, dot1); gs = 0.04; sh = 8.0;",
  "  }",
  "  else if (m < 2.5) { vec2 p = vW.xz * 2.0; vec2 g = abs(fract(p) - 0.5); float l = step(0.46, max(g.x, g.y)); alb *= (0.9 + 0.1 * h2(floor(p))) * (1.0 - 0.18 * l); gs = 0.08; }",
  "  else if (m < 3.5) { alb *= 0.9 + 0.12 * h2(floor(vW.xz * 13.0)) + 0.08 * vn(vW.xz * 0.6); gs = 0.1; sh = 12.0; }",
  "  else if (m < 4.5) { vec2 p = vW.xz * 0.5; vec2 g = abs(fract(p) - 0.5); alb *= (0.93 + 0.07 * vn(vW.xz * 1.7)) * (1.0 - 0.15 * step(0.485, max(g.x, g.y))); gs = 0.08; }",
  "  else if (m < 5.5) { float a = abs(n.x) > abs(n.z) ? vW.z : vW.x; alb *= 1.0 - 0.07 * step(0.92, fract(a * 2.0)); gs = 0.1; }",
  "  else if (m < 6.5) { alb *= 0.86 + 0.14 * sin(vU * 34.0); gs = 0.28; sh = 30.0; }",
  "  else if (m < 7.5) {",
  "    float fr = pow(1.0 - max(dot(n, -vd), 0.0), 3.0);",
  "    vec3 refl = mix(uHorz, uSkyTop, clamp(reflect(vd, n).y * 1.5 + 0.3, 0.0, 1.0));",
  "    alb = vec3(0.05, 0.075, 0.1); gs = 0.9; sh = 90.0;",
  "    em = refl * (0.18 + 0.5 * fr) * (1.0 - uNight * 0.85);",
  "    vec2 wc = floor(vW.xz * 0.6) + floor(vW.y * 0.8); float on = step(0.8, h2(wc)), dim = step(0.55, h2(wc + 9.7));",
  "    em += mix(vec3(1.0, 0.74, 0.45), vec3(0.75, 0.86, 1.0), step(0.85, h2(wc + 3.3))) * uWin * (0.02 + 0.05 * dim + 0.55 * on);",
  "  }",
  "  else if (m < 8.5) {",
  "    float fr = pow(1.0 - max(dot(n, -vd), 0.0), 2.5);",
  "    vec3 refl = mix(uHorz, uSkyTop, clamp(reflect(vd, n).y * 1.2 + 0.4, 0.0, 1.0));",
  "    alb = vC * 0.25; gs = 1.0; sh = 120.0; em = refl * (0.25 + 0.6 * fr) * (1.0 - uNight * 0.8) + vec3(1.0, 0.75, 0.45) * uWin * 0.25;",
  "  }",
  "  else if (m < 9.5) { alb = vec3(0.035, 0.038, 0.042) + vec3(0.06) * step(0.86, fract((vU - uBelt) * 2.5)); gs = 0.3; sh = 22.0; }",
  "  else if (m < 10.5) { gs = 0.7; sh = 60.0; }",
  "  else if (m < 11.5) { lit = false; em = vC * (1.0 + 1.6 * uNight); }",
  "  else if (m < 12.5) { alb = vec3(0.01); gs = 0.6; sh = 60.0; vec2 q = vec2(vW.x + vW.z, vW.y) * vec2(9.0, 14.0); em = vC * (0.35 + 0.4 * step(0.5, fract(q.y)) * step(0.3, h2(floor(q) + floor(uTime * 2.0)))) * (0.8 + 0.6 * uNight) + vec3(0.01); }",
  "  else if (m < 13.5) { float s = step(0.5, fract((vW.x + vW.y + vW.z) * 5.0)); alb = mix(vec3(0.02), vec3(0.86, 0.62, 0.05), s); gs = 0.25; }",
  "  else if (m < 14.5) { vec2 g = abs(fract(vec2(vW.x + vW.z, vW.y + vU) * vec2(5.0, 6.0)) - 0.5); alb = vec3(0.06, 0.1, 0.2) * (1.0 - 0.5 * step(0.44, max(g.x, g.y))); gs = 0.9; sh = 80.0; em = vec3(0.02, 0.04, 0.08) * (1.0 - uNight); }",
  "  else if (m < 15.5) { vec2 p = vW.xz; vec2 g = abs(fract(p) - 0.5); alb *= (0.95 + 0.05 * vn(p * 3.0)) * (1.0 - 0.06 * step(0.49, max(g.x, g.y))); gs = 0.35; sh = 40.0; }",
  "  else if (m < 16.5) {",
  "    float w1 = sin(vW.x * 3.1 + uTime * 1.3) * sin(vW.z * 2.7 - uTime * 1.1); vec3 nn = normalize(vec3(w1 * 0.08, 1.0, 0.06 * sin(vW.x * 5.0 + uTime)));",
  "    float fr = pow(1.0 - max(dot(nn, -vd), 0.0), 3.0); vec3 refl = mix(uHorz, uSkyTop, clamp(reflect(vd, nn).y * 1.5, 0.0, 1.0));",
  "    alb = vC * 0.35; n = nn; gs = 1.2; sh = 160.0; em = refl * (0.2 + 0.7 * fr) * (1.0 - 0.85 * uNight);",
  "  }",
  "  else if (m < 17.5) { float v1 = vn(vW.xz * 2.2 + vW.y * 1.7); alb *= (0.72 + 0.42 * v1) * (0.75 + 0.35 * clamp(n.y * 0.5 + 0.6, 0.0, 1.0)); gs = 0.05; sh = 8.0; em = alb * uSunC * 0.06 * max(dot(-n, uSunDir), 0.0); }",
  "  else if (m < 18.5) { em = vec3(1.0, 0.86, 0.62) * (0.15 + 3.2 * uNight); }",
  "  else if (m < 21.5) { float on;",
  "    if (m < 19.5) on = step(uAndon, 0.5); else if (m < 20.5) on = step(0.5, uAndon) * step(uAndon, 1.5); else on = step(1.5, uAndon) * step(uAndon, 2.5) * (0.55 + 0.45 * step(0.5, fract(uTime * 2.4))) + step(2.5, uAndon) * step(uAndon, 3.5);",
  "    vec3 ac = m < 19.5 ? vec3(0.15, 1.0, 0.45) : (m < 20.5 ? vec3(1.0, 0.6, 0.06) : vec3(1.0, 0.12, 0.08));",
  "    alb = ac * 0.08; gs = 0.5; em = ac * (0.04 + 2.2 * on); }",
  "  else if (m < 22.5) { alb *= 0.9 + 0.12 * vn(vec2(vW.x + vW.z, vW.y) * 18.0); gs = 0.06; }",
  "  else if (m < 23.5) {",
  "    vec2 q = gl_FragCoord.xy / uRes; float v = smoothstep(0.0, 1.0, q.y); float r = length((q - vec2(0.5, 0.55)) * vec2(1.25, 1.0));",
  "    vec3 c = mix(uBgB, uBgT, v) * (1.0 - 0.35 * smoothstep(0.35, 1.0, r));",
  "    gl_FragColor = vec4(c, 1.0); return;",
  "  }",
  "  else if (m < 24.5) { vec3 lc = alb * mix(uGndC, uSkyC, 0.7) + alb * uSunC * max(dot(n, uSunDir), 0.0) * 0.6; vec3 c = mix(lc, uFogC, 0.62);",
  "    gl_FragColor = vec4(pow(aces(c * uExp), vec3(0.4545)), 1.0); return; }",
  "  else if (m < 25.5) { alb = vec3(0.02, 0.022, 0.025); gs = 0.15; }",
  "  else if (m < 26.5) { alb = uAcc; gs = 0.35; sh = 40.0; }",
  "  else if (m < 27.5) { lit = false; em = uAcc * (0.35 + 1.6 * uAct) * (1.0 + 0.8 * uNight); }",
  "  else if (m < 28.5) { vec2 p = vec2(vW.x + vW.z, vW.y) * vec2(4.0, 8.0); p.x += step(0.5, fract(p.y * 0.5)) * 0.5; vec2 g = abs(fract(p) - 0.5); alb *= (0.85 + 0.2 * h2(floor(p))) * (1.0 - 0.3 * step(0.42, max(g.x * 0.9, g.y))); }",
  "  else if (m < 29.5) { float r = length(vW.xz), a = atan(vW.z, vW.x); float l = max(step(0.93, fract(r * 1.6)), step(0.95, fract(a * 6.0 / 3.14159 * 2.0)) * step(1.5, r)); alb *= (0.92 + 0.08 * h2(vec2(floor(r * 1.6), floor(a * 12.0)))) * (1.0 - 0.15 * l); gs = 0.1; }",
  "  else if (m < 30.5) { alb = vec3(0.04); em = vC * (0.18 + 1.6 * uNight) * (0.85 + 0.15 * sin(uTime * 3.0 + vU * 0.8)); }",
  "  else if (m < 31.5) { gs = 0.3; sh = 30.0; }",
  "  else if (m < 32.5) { alb = uPaint * vC; gs = 0.45; sh = 44.0; }",
  "  else if (m < 33.5) { gs = 0.2; sh = 24.0; }",
  "  else if (m < 34.5) { vec2 q = vW.xz * 8.0 + vec2(vW.y * 3.0); vec2 c = floor(q); float r = h2(c + 3.7); vec2 f = fract(q) - 0.5 - (vec2(h2(c + 1.1), h2(c + 2.3)) - 0.5) * 0.4;",
  "    float dt2 = (1.0 - smoothstep(0.16, 0.26, length(f))) * step(0.42, r);",
  "    vec3 fc = r > 0.86 ? vec3(0.95, 0.92, 0.86) : (r > 0.72 ? vec3(0.95, 0.8, 0.25) : (r > 0.58 ? vec3(0.66, 0.45, 0.88) : vec3(0.93, 0.48, 0.66)));",
  "    alb = mix(alb * (0.8 + 0.3 * vn(vW.xz * 3.0)), fc * fc, dt2); gs = 0.05; }",
  "  else if (m < 35.5) { vec2 p = vW.xz; float fd = clamp(1.0 - dist / 70.0, 0.0, 1.0); alb *= 0.93 + 0.06 * vn(p * 2.3) + 0.07 * (vn(p * 29.0) - 0.5) * fd; gs = 0.02; sh = 6.0; }",
  "  else { vec2 p = abs(n.y) > 0.5 ? vW.xz : vec2(vW.x + vW.z, vW.y); float row = floor(p.y * 4.0); float off = h2(vec2(row, 3.1)) * 4.0; float fd = clamp(1.0 - dist / 70.0, 0.0, 1.0);",
  "    float gr = vn(vec2((p.x + off) * 1.4, row * 7.3)) * 0.6 + vn(vec2((p.x + off) * 7.0, p.y * 36.0)) * 0.4;",
  "    float sm = max(step(0.94, fract(p.y * 4.0)), step(0.988, fract((p.x + off) * 0.42))) * fd;",
  "    alb *= (0.84 + 0.12 * h2(vec2(row, floor((p.x + off) * 0.42))) + 0.1 * gr) * (1.0 - 0.22 * sm); gs = 0.24; sh = 32.0; }",
  "  float sd = 1.0;",
  "  if (lit) { sd = shadowAt(vW, n);",
  "    if (uCloud > 0.01) { float cc = vn(vW.xz * 0.035 + vec2(uTime * 0.012, uTime * 0.006)) * 0.65 + vn(vW.xz * 0.09 - vec2(uTime * 0.02, 0.0)) * 0.35; sd *= mix(1.0, 0.5, smoothstep(0.5, 0.72, cc) * uCloud); } }",
  "  vec3 col = (lit ? lightIt(vW, n, alb, gs, sh, sd) : vec3(0.0)) + em;",
  "  if (lit && abs(n.y) < 0.75 && m > 1.5 && vW.y > -0.5) col *= mix(0.66, 1.0, smoothstep(0.0, 0.6, vW.y));",
  "  float f = 1.0 - exp(-max(dist - uFogS, 0.0) * uFogD);",
  "  col = mix(col, uFogC, f);",
  "  col = pow(aces(col * uExp), vec3(0.4545));",
  "  gl_FragColor = vec4(col, 1.0);",
  "}"
].join("\n");
/* 阴影：深度打包进 RGBA */
var VS_SH = "precision highp float; attribute vec3 aP; uniform mat4 uVP; uniform mat4 uModel; void main(){ gl_Position = uVP * (uModel * vec4(aP, 1.0)); }";
var FS_SH = HP + "vec4 pack(float d){ vec4 e = vec4(1.0, 255.0, 65025.0, 16581375.0) * d; e = fract(e); e -= e.yzww * vec4(1.0 / 255.0, 1.0 / 255.0, 1.0 / 255.0, 0.0); return e; } void main(){ gl_FragColor = pack(gl_FragCoord.z); }";
/* 特效层（世界坐标的面片）：0 光点 1 光痕 2 光幕 3 光环 4 光束 5 地面光池 6 烟团（叠加用另一种混合） */
var VS_FX = [
  "precision highp float;",
  "attribute vec3 aP; attribute vec2 aUV; attribute vec4 aC; attribute float aK;",
  "uniform mat4 uVP;",
  "varying vec2 vUV; varying vec4 vC; varying float vK;",
  "void main(){ vUV = aUV; vC = aC; vK = aK; gl_Position = uVP * vec4(aP, 1.0); }"
].join("\n");
var FS_FX = [
  "precision mediump float;",
  "varying vec2 vUV; varying vec4 vC; varying float vK;",
  "void main(){",
  "  vec2 q = vUV; float a;",
  "  if (vK < 0.5) { float d2 = dot(q, q); a = (exp(-d2 * 4.0) * 0.55 + exp(-d2 * 26.0)) * (1.0 - smoothstep(0.9, 1.0, sqrt(d2))); }",
  "  else if (vK < 1.5) { a = exp(-q.x * q.x * 3.5) * (0.25 + 0.75 * q.y); }",
  "  else if (vK < 2.5) { float e = min(min(q.x, 1.0 - q.x), min(q.y, 1.0 - q.y)); a = 0.16 + 0.84 * exp(-e * 38.0); }",
  "  else if (vK < 3.5) { float d = length(q); a = exp(-pow((d - 0.82) / 0.07, 2.0)) + exp(-d * d * 3.0) * 0.12; a *= 1.0 - smoothstep(0.95, 1.0, d); }",
  "  else if (vK < 4.5) { a = exp(-q.x * q.x * 2.2) * (1.0 - q.y) * (0.35 + 0.65 * q.y); }",
  "  else if (vK < 5.5) { float d2 = dot(q, q); a = exp(-d2 * 2.6) * (1.0 - smoothstep(0.85, 1.0, sqrt(d2))); }",
  "  else { float d2 = dot(q, q); a = exp(-d2 * 3.0) * (1.0 - smoothstep(0.8, 1.0, sqrt(d2))); a *= vC.a; gl_FragColor = vec4(vC.rgb * a, a); return; }",
  "  a *= vC.a;",
  "  gl_FragColor = vec4(vC.rgb * a, a);",
  "}"
].join("\n");
var VS_Q = "precision highp float; attribute vec2 aP; varying vec2 vUV; void main(){ vUV = aP * 0.5 + 0.5; gl_Position = vec4(aP, 0.0, 1.0); }";
var FS_DOWN = "precision mediump float; varying vec2 vUV; uniform sampler2D uT; uniform vec2 uPx; uniform float uTh; void main(){ vec4 s = texture2D(uT, vUV + uPx * vec2(-1.0, -1.0)) + texture2D(uT, vUV + uPx * vec2(1.0, -1.0)) + texture2D(uT, vUV + uPx * vec2(-1.0, 1.0)) + texture2D(uT, vUV + uPx * vec2(1.0, 1.0)); vec3 c = max(s.rgb * 0.25 - uTh, 0.0); gl_FragColor = vec4(c, 1.0); }";
var FS_BLUR = [
  "precision mediump float; varying vec2 vUV; uniform sampler2D uT; uniform vec2 uDir;",
  "void main(){",
  "  vec4 s = texture2D(uT, vUV) * 0.227027;",
  "  s += (texture2D(uT, vUV + uDir) + texture2D(uT, vUV - uDir)) * 0.1945946;",
  "  s += (texture2D(uT, vUV + uDir * 2.0) + texture2D(uT, vUV - uDir * 2.0)) * 0.1216216;",
  "  s += (texture2D(uT, vUV + uDir * 3.0) + texture2D(uT, vUV - uDir * 3.0)) * 0.054054;",
  "  s += (texture2D(uT, vUV + uDir * 4.0) + texture2D(uT, vUV - uDir * 4.0)) * 0.016216;",
  "  gl_FragColor = s;",
  "}"
].join("\n");
var FS_COMP = [
  "precision mediump float; varying vec2 vUV; uniform sampler2D uT; uniform sampler2D uTilt; uniform sampler2D uBloom;",
  "uniform float uFocus, uBand, uTiltK, uBloomK, uSat, uFade;",
  "void main(){",
  "  vec3 s = texture2D(uT, vUV).rgb, b = texture2D(uTilt, vUV).rgb;",
  "  float d = abs(vUV.y - uFocus); float m = smoothstep(uBand, uBand + 0.3, d) * uTiltK;",
  "  vec3 c = mix(s, b, m) + texture2D(uBloom, vUV).rgb * uBloomK;",
  "  float l = dot(c, vec3(0.299, 0.587, 0.114)); c = mix(vec3(l), c, uSat);",
  "  gl_FragColor = vec4(c * (1.0 - uFade), 1.0);",
  "}"
].join("\n");

var P_MAIN, P_SH, P_FX, P_DOWN, P_BLUR, P_COMP, B = {}, FB = null, SHT = null, SHS = 2048;
function U(p, n) { if (!(n in p.u)) p.u[n] = gl.getUniformLocation(p, n); return p.u[n]; }
function A(p, n) { if (!(n in p.a)) p.a[n] = gl.getAttribLocation(p, n); return p.a[n]; }
var BOUND = [];
function attr(p, n, buf, size, stride, off) { var l = A(p, n); if (l < 0) return; gl.bindBuffer(gl.ARRAY_BUFFER, buf); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, size, gl.FLOAT, false, stride || 0, off || 0); BOUND.push(l); }
function unbindAll() { BOUND.forEach(function (l) { gl.disableVertexAttribArray(l); }); BOUND = []; }
var UINT = false;
function upload(g) {
  g.vb = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, g.vb); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(g.D), gl.STATIC_DRAW);
  g.big = g.n > 65535;
  g.ib = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, g.ib); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, g.big ? new Uint32Array(g.I) : new Uint16Array(g.I), gl.STATIC_DRAW);
  g.cnt = g.I.length;
  if (!g.keep) { g.D = null; g.I = null; }
}
function glSetup() {
  function shd(type, src) { var s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) console.error(gl.getShaderInfoLog(s)); return s; }
  function prog(vs, fs) { var p = gl.createProgram(); gl.attachShader(p, shd(gl.VERTEX_SHADER, vs)); gl.attachShader(p, shd(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p); if (!gl.getProgramParameter(p, gl.LINK_STATUS)) console.error(gl.getProgramInfoLog(p)); p.u = {}; p.a = {}; return p; }
  P_MAIN = prog(VS_MAIN, FS_MAIN); P_SH = prog(VS_SH, FS_SH); P_FX = prog(VS_FX, FS_FX);
  P_DOWN = prog(VS_Q, FS_DOWN); P_BLUR = prog(VS_Q, FS_BLUR); P_COMP = prog(VS_Q, FS_COMP);
  UINT = GL2 || !!gl.getExtension("OES_element_index_uint");
  GEO_ALL.forEach(function (g) { if (g.n > 65535 && !UINT) { g.I = g.I.slice(0, 0); } upload(g); });
  B.fxa = gl.createBuffer(); B.fxb = gl.createBuffer();
  B.q = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, B.q); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  B.bv = gl.createBuffer(); B.bi = gl.createBuffer();
  /* 阴影贴图 */
  SHT = mkTarget(SHS, SHS, true, true);
}
function mkTarget(w, h, depth, nearest) {
  var t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
  var flt = nearest ? gl.NEAREST : gl.LINEAR;
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, flt); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, flt);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  var f = gl.createFramebuffer(), rb = null; gl.bindFramebuffer(gl.FRAMEBUFFER, f);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
  if (depth) {
    rb = gl.createRenderbuffer(); gl.bindRenderbuffer(gl.RENDERBUFFER, rb);
    if (depth === 2) { gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_STENCIL, w, h); gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_STENCIL_ATTACHMENT, gl.RENDERBUFFER, rb); }
    else { gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT16, w, h); gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, rb); }
  }
  var ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  return { t: t, f: f, rb: rb, w: w, h: h, ok: ok };
}
function freeTarget(x) { if (x) { gl.deleteTexture(x.t); gl.deleteFramebuffer(x.f); if (x.rb) gl.deleteRenderbuffer(x.rb); } }
function mkMS(w, h) {
  if (!GL2) return null;
  var s = Math.min(4, gl.getParameter(gl.MAX_SAMPLES) || 0); if (s < 2) return null;
  var f = gl.createFramebuffer(), c = gl.createRenderbuffer(), d = gl.createRenderbuffer();
  gl.bindFramebuffer(gl.FRAMEBUFFER, f);
  gl.bindRenderbuffer(gl.RENDERBUFFER, c); gl.renderbufferStorageMultisample(gl.RENDERBUFFER, s, gl.RGBA8, w, h); gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.RENDERBUFFER, c);
  gl.bindRenderbuffer(gl.RENDERBUFFER, d); gl.renderbufferStorageMultisample(gl.RENDERBUFFER, s, gl.DEPTH_COMPONENT24, w, h); gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, d);
  var ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  if (!ok) { gl.deleteFramebuffer(f); gl.deleteRenderbuffer(c); gl.deleteRenderbuffer(d); return null; }
  return { f: f, c: c, d: d };
}
function ensureFB() {
  var w = glc.width, h = glc.height;
  if (FB && FB.w === w && FB.h === h) return FB.ok;
  if (FB) { freeTarget(FB.a); freeTarget(FB.h1); freeTarget(FB.h2); freeTarget(FB.q1); freeTarget(FB.q2); if (FB.ms) { gl.deleteFramebuffer(FB.ms.f); gl.deleteRenderbuffer(FB.ms.c); gl.deleteRenderbuffer(FB.ms.d); } }
  var hw = Math.max(1, Math.ceil(w / 2)), hh = Math.max(1, Math.ceil(h / 2)), qw = Math.max(1, Math.ceil(w / 4)), qh = Math.max(1, Math.ceil(h / 4));
  FB = { w: w, h: h, a: mkTarget(w, h, 2), h1: mkTarget(hw, hh), h2: mkTarget(hw, hh), q1: mkTarget(qw, qh), q2: mkTarget(qw, qh), hw: hw, hh: hh, qw: qw, qh: qh, ms: mkMS(w, h) };
  FB.ok = FB.a.ok && FB.h1.ok && FB.h2.ok && FB.q1.ok && FB.q2.ok;
  return FB.ok;
}
function quadDraw(P, tex, unit) { gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, tex); gl.uniform1i(U(P, "uT"), 0); attr(P, "aP", B.q, 2); gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4); unbindAll(); }

/* ---------- 特效面片缓冲：A 叠加发光，B 烟雾（正常混合） ---------- */
function FXBuf(n) { return { v: new Float32Array(n * 10), n: 0, max: n }; }
var FXA = FXBuf(36000), FXB = FXBuf(6000), FXT = FXA;
var CAMR = [1, 0, 0], CAMU = [0, 1, 0], CAMP = [0, 20, 40];
function fxv(p, u, v, c, a, k) { var X = FXT; if (X.n >= X.max) return; var o = X.n * 10, V = X.v; V[o] = p[0]; V[o + 1] = p[1]; V[o + 2] = p[2]; V[o + 3] = u; V[o + 4] = v; V[o + 5] = c[0]; V[o + 6] = c[1]; V[o + 7] = c[2]; V[o + 8] = a; V[o + 9] = k; X.n++; }
function fxQuad(p0, p1, p2, p3, uv, c, a, k) { fxv(p0, uv[0], uv[1], c, a, k); fxv(p1, uv[2], uv[3], c, a, k); fxv(p2, uv[4], uv[5], c, a, k); fxv(p0, uv[0], uv[1], c, a, k); fxv(p2, uv[4], uv[5], c, a, k); fxv(p3, uv[6], uv[7], c, a, k); }
var UVS = [-1, -1, 1, -1, 1, 1, -1, 1], UV01 = [0, 0, 1, 0, 1, 1, 0, 1];
function fxDot(p, r, c, a) { if (a < 0.004) return; var R = v3sc(CAMR, r), Uu = v3sc(CAMU, r); fxQuad(v3sub(v3sub(p, R), Uu), v3sub(v3add(p, R), Uu), v3add(v3add(p, R), Uu), v3add(v3sub(p, R), Uu), UVS, c, a, 0); }
function fxPuff(p, r, c, a) { if (a < 0.004) return; FXT = FXB; var R = v3sc(CAMR, r), Uu = v3sc(CAMU, r); fxQuad(v3sub(v3sub(p, R), Uu), v3sub(v3add(p, R), Uu), v3add(v3add(p, R), Uu), v3add(v3sub(p, R), Uu), UVS, c, a, 6); FXT = FXA; }
function fxStreak(p0, p1, w, c, a, k) {
  if (a < 0.004) return;
  var d = v3sub(p1, p0), mid = v3lerp(p0, p1, 0.5), vd = v3sub(mid, CAMP), s = v3x(d, vd), l = v3len(s);
  if (l < 1e-6) return; s = v3sc(s, w / l);
  fxQuad(v3sub(p0, s), v3add(p0, s), v3add(p1, s), v3sub(p1, s), [-1, 0, 1, 0, 1, 1, -1, 1], c, a, k === undefined ? 1 : k);
}
function fxRingH(p, r, c, a) { if (a < 0.004) return; fxQuad([p[0] - r, p[1], p[2] - r], [p[0] + r, p[1], p[2] - r], [p[0] + r, p[1], p[2] + r], [p[0] - r, p[1], p[2] + r], UVS, c, a, 3); }
function fxPool(p, r, c, a) { if (a < 0.004) return; fxQuad([p[0] - r, p[1], p[2] - r], [p[0] + r, p[1], p[2] - r], [p[0] + r, p[1], p[2] + r], [p[0] - r, p[1], p[2] + r], UVS, c, a, 5); }
function fxBeam(p0, p1, w0, w1, c, al) {
  var d = v3sub(p1, p0), mid = v3lerp(p0, p1, 0.5), sd = v3x(d, v3sub(mid, CAMP)), l = v3len(sd); if (l < 1e-6) return; sd = v3sc(sd, 1 / l);
  fxQuad(v3sub(p0, v3sc(sd, w0)), v3add(p0, v3sc(sd, w0)), v3add(p1, v3sc(sd, w1)), v3sub(p1, v3sc(sd, w1)), [-1, 0, 1, 0, 1, 1, -1, 1], c, al, 4);
}

/* ---------- 批量绘制：人、文件夹每帧按矩阵变换拼进一个缓冲（衣服 / 头发 / 光环三个色槽可换色） ---------- */
var BAT = { v: null, i: null, nv: 0, ni: 0, MV: 140000, MI: 240000, dirty: true };
function batInit() {
  if (!UINT) BAT.MV = 65535;
  BAT.v = new Float32Array(BAT.MV * 11);
  BAT.i = UINT ? new Uint32Array(BAT.MI) : new Uint16Array(BAT.MI);
}
function batAddM(g, M, cA, cB, cC) {
  var D = g.D, I = g.I, n = g.n, ni = I.length;
  if (BAT.nv + n > BAT.MV || BAT.ni + ni > BAT.MI) return;
  var V = BAT.v, o = BAT.nv * 11, j;
  for (j = 0; j < n; j++) {
    var b = j * 11, px = D[b], py = D[b + 1], pz = D[b + 2], nx = D[b + 3], ny = D[b + 4], nz = D[b + 5], q = o + j * 11, m = D[b + 9];
    V[q] = M[0] * px + M[4] * py + M[8] * pz + M[12]; V[q + 1] = M[1] * px + M[5] * py + M[9] * pz + M[13]; V[q + 2] = M[2] * px + M[6] * py + M[10] * pz + M[14];
    V[q + 3] = M[0] * nx + M[4] * ny + M[8] * nz; V[q + 4] = M[1] * nx + M[5] * ny + M[9] * nz; V[q + 5] = M[2] * nx + M[6] * ny + M[10] * nz;
    var C = (m === MTAPE && cA) ? cA : ((m === MSLOT2 && cB) ? cB : ((m === MEMIT && cC) ? cC : null));
    if (C) { V[q + 6] = C[0]; V[q + 7] = C[1]; V[q + 8] = C[2]; } else { V[q + 6] = D[b + 6]; V[q + 7] = D[b + 7]; V[q + 8] = D[b + 8]; }
    V[q + 9] = m; V[q + 10] = D[b + 10];
  }
  var IB = BAT.i, base = BAT.nv, oi = BAT.ni;
  for (j = 0; j < ni; j++) IB[oi + j] = I[j] + base;
  BAT.nv += n; BAT.ni += ni;
}
/* ---------- 每帧绘制 ---------- */
var VP = null, VIEWM = null, SMAT = null, DRAWS = [], ENV = null, LPA = new Float32Array(24), LCA = new Float32Array(24), IDM = m4T(0, 0, 0);
/* g 网格, M 矩阵, acc 强调色, act 活跃度, andon 塔灯, belt 带面偏移, cast 是否投影 */
var PAINT0 = [0.85, 0.85, 0.85];
function dq(g, M, acc, act, andon, belt, cast, paint) { DRAWS.push({ g: g, M: M || IDM, acc: acc || [0.4, 0.9, 0.5], act: act || 0, andon: andon === undefined ? 9 : andon, belt: belt || 0, cast: cast !== false, paint: paint || PAINT0 }); }
function bindGeo(P, g, full) {
  gl.bindBuffer(gl.ARRAY_BUFFER, g.vb);
  var l = A(P, "aP"); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, 3, gl.FLOAT, false, 44, 0); BOUND.push(l);
  if (full) {
    l = A(P, "aN"); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, 3, gl.FLOAT, false, 44, 12); BOUND.push(l);
    l = A(P, "aC"); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, 3, gl.FLOAT, false, 44, 24); BOUND.push(l);
    l = A(P, "aM"); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, 1, gl.FLOAT, false, 44, 36); BOUND.push(l);
    l = A(P, "aU"); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, 1, gl.FLOAT, false, 44, 40); BOUND.push(l);
  }
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, g.ib);
}
function drawList(P, full, castOnly) {
  var i, lastG = null;
  for (i = 0; i < DRAWS.length; i++) {
    var d = DRAWS[i], g = d.g;
    if (castOnly && !d.cast) continue;
    if (g !== lastG) { unbindAll(); bindGeo(P, g, full); lastG = g; }
    gl.uniformMatrix4fv(U(P, "uModel"), false, d.M);
    if (full) { gl.uniform3fv(U(P, "uAcc"), d.acc); gl.uniform1f(U(P, "uAct"), d.act); gl.uniform1f(U(P, "uAndon"), d.andon); gl.uniform1f(U(P, "uBelt"), d.belt); gl.uniform3fv(U(P, "uPaint"), d.paint); }
    gl.drawElements(gl.TRIANGLES, g.cnt, g.big ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT, 0);
  }
  unbindAll();
  /* 工单箱批量 */
  if (BAT.ni) {
    gl.bindBuffer(gl.ARRAY_BUFFER, B.bv);
    if (BAT.dirty) { gl.bufferData(gl.ARRAY_BUFFER, BAT.v.subarray(0, BAT.nv * 11), gl.STREAM_DRAW); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, B.bi); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, BAT.i.subarray(0, BAT.ni), gl.STREAM_DRAW); BAT.dirty = false; gl.bindBuffer(gl.ARRAY_BUFFER, B.bv); }
    var l = A(P, "aP"); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, 3, gl.FLOAT, false, 44, 0); BOUND.push(l);
    if (full) {
      l = A(P, "aN"); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, 3, gl.FLOAT, false, 44, 12); BOUND.push(l);
      l = A(P, "aC"); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, 3, gl.FLOAT, false, 44, 24); BOUND.push(l);
      l = A(P, "aM"); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, 1, gl.FLOAT, false, 44, 36); BOUND.push(l);
      l = A(P, "aU"); gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, 1, gl.FLOAT, false, 44, 40); BOUND.push(l);
    }
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, B.bi);
    gl.uniformMatrix4fv(U(P, "uModel"), false, IDM);
    if (full) { gl.uniform3fv(U(P, "uAcc"), [1.0, 0.78, 0.3]); gl.uniform1f(U(P, "uAct"), 0.55); gl.uniform1f(U(P, "uAndon"), 9); gl.uniform1f(U(P, "uBelt"), 0); gl.uniform3fv(U(P, "uPaint"), PAINT0); }
    gl.drawElements(gl.TRIANGLES, BAT.ni, UINT ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT, 0);
    unbindAll();
  }
}
function fxDraw(X, P) {
  if (!X.n) return;
  gl.bindBuffer(gl.ARRAY_BUFFER, X === FXA ? B.fxa : B.fxb); gl.bufferData(gl.ARRAY_BUFFER, X.v.subarray(0, X.n * 10), gl.STREAM_DRAW);
  attr(P, "aP", X === FXA ? B.fxa : B.fxb, 3, 40, 0); attr(P, "aUV", X === FXA ? B.fxa : B.fxb, 2, 40, 12); attr(P, "aC", X === FXA ? B.fxa : B.fxb, 4, 40, 20); attr(P, "aK", X === FXA ? B.fxa : B.fxb, 1, 40, 36);
  gl.drawArrays(gl.TRIANGLES, 0, X.n); unbindAll();
}
function drawAll(t) {
  var post = ensureFB(), P, i;
  /* 1. 阴影 */
  if (E.shOn && SHT.ok) {
    gl.bindFramebuffer(gl.FRAMEBUFFER, SHT.f); gl.viewport(0, 0, SHS, SHS);
    gl.clearColor(1, 1, 1, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.depthMask(true); gl.disable(gl.BLEND); gl.disable(gl.CULL_FACE);
    P = P_SH; gl.useProgram(P); gl.uniformMatrix4fv(U(P, "uVP"), false, SMAT);
    drawList(P, false, true);
  }
  /* 2. 主画面 */
  var useMS = FB && FB.ms && Q.ms;
  var tgt = post ? (useMS ? FB.ms.f : FB.a.f) : null;
  gl.bindFramebuffer(gl.FRAMEBUFFER, tgt); gl.viewport(0, 0, glc.width, glc.height);
  gl.clearColor(E.bgB[0], E.bgB[1], E.bgB[2], 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.depthMask(true); gl.disable(gl.BLEND); gl.disable(gl.CULL_FACE);
  P = P_MAIN; gl.useProgram(P);
  gl.uniformMatrix4fv(U(P, "uVP"), false, VP);
  gl.uniform3fv(U(P, "uCam"), CAMP); gl.uniform1f(U(P, "uTime"), t);
  gl.uniform1f(U(P, "uNight"), E.night); gl.uniform1f(U(P, "uWin"), E.win); gl.uniform1f(U(P, "uExp"), E.exp);
  gl.uniform3fv(U(P, "uSunDir"), E.sunDir); gl.uniform3fv(U(P, "uSunC"), E.sunC); gl.uniform3fv(U(P, "uSkyC"), E.skyC); gl.uniform3fv(U(P, "uGndC"), E.gndC);
  gl.uniform3fv(U(P, "uFogC"), E.fogL); gl.uniform3fv(U(P, "uSkyTop"), E.top); gl.uniform3fv(U(P, "uHorz"), E.hor);
  gl.uniform3fv(U(P, "uBgT"), E.bgT); gl.uniform3fv(U(P, "uBgB"), E.bgB); gl.uniform2f(U(P, "uRes"), glc.width, glc.height);
  gl.uniform1f(U(P, "uFogD"), E.fogD); gl.uniform1f(U(P, "uCloud"), E.cloud || 0); gl.uniform1f(U(P, "uFogS"), E.fogS);
  gl.uniform1f(U(P, "uShOn"), E.shOn && SHT.ok ? 1 : 0); gl.uniform1f(U(P, "uShB"), E.shB); gl.uniform1f(U(P, "uShN"), E.shN); gl.uniform2f(U(P, "uShPx"), 1 / SHS, 1 / SHS);
  if (SMAT) gl.uniformMatrix4fv(U(P, "uSM"), false, SMAT);
  gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, SHT.t); gl.uniform1i(U(P, "uShadow"), 1); gl.activeTexture(gl.TEXTURE0);
  gl.uniform3fv(U(P, "uLP[0]"), LPA); gl.uniform3fv(U(P, "uLC[0]"), LCA);
  /* 天空先画、不写深度 */
  gl.depthMask(false);
  bindGeo(P, SKY, true); gl.uniformMatrix4fv(U(P, "uModel"), false, m4T(CAMP[0], 0, CAMP[2])); gl.drawElements(gl.TRIANGLES, SKY.cnt, gl.UNSIGNED_SHORT, 0); unbindAll();
  gl.depthMask(true);
  drawList(P, true, false);
  /* 特效 */
  gl.enable(gl.BLEND); gl.depthMask(false);
  P = P_FX; gl.useProgram(P); gl.uniformMatrix4fv(U(P, "uVP"), false, VP);
  gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA); fxDraw(FXB, P);
  gl.blendFunc(gl.ONE, gl.ONE); fxDraw(FXA, P);
  gl.depthMask(true); gl.disable(gl.DEPTH_TEST); gl.disable(gl.BLEND);
  if (!post) return;
  if (useMS) {
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, FB.ms.f); gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, FB.a.f);
    gl.blitFramebuffer(0, 0, FB.w, FB.h, 0, 0, FB.w, FB.h, gl.COLOR_BUFFER_BIT, gl.NEAREST);
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, null); gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, null);
  }
  /* 3. 移轴：半分辨率模糊两遍 */
  gl.bindFramebuffer(gl.FRAMEBUFFER, FB.h1.f); gl.viewport(0, 0, FB.hw, FB.hh);
  P = P_DOWN; gl.useProgram(P); gl.uniform2f(U(P, "uPx"), 0.5 / FB.w, 0.5 / FB.h); gl.uniform1f(U(P, "uTh"), 0.0); quadDraw(P, FB.a.t);
  P = P_BLUR; gl.useProgram(P);
  for (i = 0; i < 2; i++) {
    var sp = i ? 2.2 : 1.2;
    gl.bindFramebuffer(gl.FRAMEBUFFER, FB.h2.f); gl.uniform2f(U(P, "uDir"), sp / FB.hw, 0); quadDraw(P, FB.h1.t);
    gl.bindFramebuffer(gl.FRAMEBUFFER, FB.h1.f); gl.uniform2f(U(P, "uDir"), 0, sp / FB.hh); quadDraw(P, FB.h2.t);
  }
  /* 4. 泛光：四分之一分辨率 */
  gl.bindFramebuffer(gl.FRAMEBUFFER, FB.q1.f); gl.viewport(0, 0, FB.qw, FB.qh);
  P = P_DOWN; gl.useProgram(P); gl.uniform2f(U(P, "uPx"), 1 / FB.w, 1 / FB.h); gl.uniform1f(U(P, "uTh"), E.bloomTh); quadDraw(P, FB.a.t);
  P = P_BLUR; gl.useProgram(P);
  for (i = 0; i < 2; i++) {
    var sq = i ? 2.4 : 1.2;
    gl.bindFramebuffer(gl.FRAMEBUFFER, FB.q2.f); gl.uniform2f(U(P, "uDir"), sq / FB.qw, 0); quadDraw(P, FB.q1.t);
    gl.bindFramebuffer(gl.FRAMEBUFFER, FB.q1.f); gl.uniform2f(U(P, "uDir"), 0, sq / FB.qh); quadDraw(P, FB.q2.t);
  }
  /* 5. 合成到屏幕 */
  gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.viewport(0, 0, glc.width, glc.height);
  P = P_COMP; gl.useProgram(P);
  gl.uniform1f(U(P, "uFocus"), E.focus); gl.uniform1f(U(P, "uBand"), E.band); gl.uniform1f(U(P, "uTiltK"), E.tilt); gl.uniform1f(U(P, "uBloomK"), E.bloomK); gl.uniform1f(U(P, "uSat"), E.sat); gl.uniform1f(U(P, "uFade"), E.fade);
  gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, FB.h1.t); gl.uniform1i(U(P, "uTilt"), 1);
  gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, FB.q1.t); gl.uniform1i(U(P, "uBloom"), 2);
  gl.activeTexture(gl.TEXTURE0);
  quadDraw(P, FB.a.t);
}
if (gl) { glSetup(); batInit(); }

/* =====================================================================
   14. 布局：1920×1080 画板按窗口缩放；镜头把目标放在中间留给 3D 的区域中心
   ===================================================================== */
var L = { w: 1, h: 1, bs: 0.5, frX: 0, frY: 0, dpr: 1, dpr2: 1, narrow: false, ok: false, rx0: 0, rx1: 1, ry0: 0, ry1: 1 };
var fx = fxc.getContext("2d");
var wasNarrow = null, Q = { sh: true, ms: true, dpr: 0, auto: true };
function layout() {
  if (host.hidden || host.clientWidth < 2) return;
  var appR = host.getBoundingClientRect();
  L.w = Math.max(1, appR.width); L.h = Math.max(1, appR.height);
  L.narrow = L.w < 760 || L.w / L.h < 1.05;
  nr.classList.toggle("narrow", L.narrow);
  var fr = fitIn.getBoundingClientRect();
  L.bs = Math.min(fr.width / 1920, fr.height / 1080) || 0.2;
  var bw = fr.width / L.bs, bh = fr.height / L.bs;
  board.style.width = bw + "px"; board.style.height = bh + "px";
  board.style.setProperty("--s", L.bs);
  L.frX = fr.left - appR.left; L.frY = fr.top - appR.top;
  var d0 = Math.min(window.devicePixelRatio || 1, 1.5);
  L.dpr = Q.dpr || Math.max(0.6, Math.min(d0, Math.sqrt(3.4e6 / Math.max(1, L.w * L.h))));
  L.dpr2 = Math.min(window.devicePixelRatio || 1, 2);
  glc.width = Math.round(L.w * L.dpr); glc.height = Math.round(L.h * L.dpr);
  fxc.width = Math.round(L.w * L.dpr2); fxc.height = Math.round(L.h * L.dpr2);
  /* 3D 留给中间：左右各让出背景数据的宽度，下边让出底部一条 */
  if (!L.narrow) { L.rx0 = L.frX + 330 * L.bs; L.rx1 = L.frX + (bw - 330) * L.bs; L.ry0 = L.frY + 70 * L.bs; L.ry1 = L.frY + (bh - 236) * L.bs; }
  else { L.rx0 = 0; L.rx1 = L.w; L.ry0 = 70; L.ry1 = L.h - 170; }
  LABELS.concat(RLAB, [CHL]).forEach(function (Lb) { Lb.w = Lb.el.offsetWidth || 180; Lb.h = Lb.el.offsetHeight || 56; });
  L.ok = true;
  if (wasNarrow !== L.narrow) { wasNarrow = L.narrow; snapCam(); }
}

/* =====================================================================
   15. 昼夜：实时 / 白天 / 黄昏 / 夜晚 / 循环（120 秒一昼夜）
   ===================================================================== */
var TOD = { mode: 0, names: ["实时", "白天", "黄昏", "夜晚", "循环"], h: 12 };
var now = 0;
function todHour() {
  if (TOD.mode === 0) { var d = new Date(); return d.getHours() + d.getMinutes() / 60 + d.getSeconds() / 3600; }
  if (TOD.mode === 1) return 10.6;
  if (TOD.mode === 2) return 17.75;
  if (TOD.mode === 3) return 22.4;
  return (9 + now / 120 * 24) % 24;
}
function L3(r, g, b, k) { var c = lin([r, g, b]); return [c[0] * k, c[1] * k, c[2] * k]; }
function mixL(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }
var E = { sunDir: [0.4, 0.7, 0.5], sunC: [1, 1, 1], skyC: [0.3, 0.3, 0.3], gndC: [0.1, 0.1, 0.1], top: [0.2, 0.4, 0.8], hor: [0.7, 0.8, 0.9], fogL: [0.7, 0.8, 0.9], fog: [0.8, 0.85, 0.9],
  fogD: 0.006, fogS: 80, bgT: [0.25, 0.29, 0.34], bgB: [0.1, 0.12, 0.15], night: 0, win: 0, exp: 1, shOn: true, shB: 0.0006, shN: 0.06, bloomTh: 0.7, bloomK: 0.5, focus: 0.5, band: 0.16, tilt: 1, sat: 1.06, fade: 0, lamp: 0 };
function envUpdate() {
  var h = todHour(); TOD.h = h;
  var el = Math.sin((h - 6) / 12 * Math.PI), phi = (205 + (h - 12) * 7) * Math.PI / 180;
  var D = smooth(-0.06, 0.22, el), K = Math.exp(-Math.pow((el - 0.06) / 0.16, 2)), N = 1 - smooth(-0.16, 0.04, el);
  var Ea = Math.max(0.05, el) * 0.95;
  var sun = [Math.cos(Ea) * Math.cos(phi), Math.sin(Ea), Math.cos(Ea) * Math.sin(phi)];
  var moon = v3n([-0.6, 0.7, 0.4]);
  var useSun = el > -0.02;
  E.sunDir = useSun ? v3n(sun) : moon;
  var sd = mixL(L3(1.0, 0.95, 0.86, 2.0), L3(1.0, 0.58, 0.32, 1.85), K);
  E.sunC = useSun ? v3sc(sd, D) : L3(0.55, 0.66, 0.95, 0.38 * N);
  E.skyC = mixL(mixL(L3(0.66, 0.72, 0.86, 0.56), L3(0.78, 0.6, 0.55, 0.42), K * 0.8), L3(0.32, 0.36, 0.55, 0.26), N);
  E.gndC = mixL(L3(0.62, 0.55, 0.45, 0.36), L3(0.16, 0.14, 0.15, 0.16), N);
  E.top = mixL(mixL(L3(0.3, 0.53, 0.86, 1), L3(0.3, 0.36, 0.62, 1), K * 0.85), L3(0.02, 0.03, 0.07, 1), N);
  E.hor = mixL(mixL(L3(0.82, 0.86, 0.9, 1), L3(1.0, 0.64, 0.42, 1), K * 0.9), L3(0.07, 0.08, 0.13, 1), N);
  /* 背景罩：白天深石板灰、黄昏带一点暖、夜里深蓝黑（显示空间颜色，直接输出） */
  E.bgT = mix3(mix3([0.25, 0.29, 0.34], [0.33, 0.27, 0.28], K * 0.8), [0.075, 0.095, 0.135], N);
  E.bgB = mix3(mix3([0.10, 0.12, 0.15], [0.13, 0.10, 0.12], K * 0.8), [0.03, 0.035, 0.055], N);
  E.fogL = lin(mix3(E.bgT, E.bgB, 0.4));
  E.night = N; E.win = clamp(N * 1.1 + K * 0.3, 0, 1); E.lamp = clamp(N * 1.2 + K * 0.25, 0, 1);
  E.cloud = clamp(1 - N * 1.4, 0, 1) * 0.6;
  E.exp = 0.9 + 0.42 * N;
  E.bloomTh = lerp(0.76, 0.46, N); E.bloomK = lerp(0.38, 1.0, N);
  var fc = E.fogL.map(function (x) { var y = x * E.exp, a = (y * (2.51 * y + 0.03)) / (y * (2.43 * y + 0.59) + 0.14); return Math.pow(clamp(a, 0, 1), 0.4545); });
  E.fog = fc;
}

/* =====================================================================
   16. 走路网格：走廊 / 总经理办公室 / 大堂连成图（入职从大门台阶进来，离职走到台阶离开）；每个座位、房间位都有一段「出门路线」接到图上的节点
   ===================================================================== */
var NODES = [], NADJ = [];
function nAdd(x, z) { for (var i = 0; i < NODES.length; i++) if (Math.abs(NODES[i][0] - x) < 0.04 && Math.abs(NODES[i][1] - z) < 0.04) return i; NODES.push([x, z]); NADJ.push([]); return NODES.length - 1; }
function nLink(a, b) { if (a === b) return; var d = hyp(NODES[a][0] - NODES[b][0], NODES[a][1] - NODES[b][1]); NADJ[a].push([b, d]); NADJ[b].push([a, d]); }
function doorNode(R) { var d = rw(R, R.doorU, R.hd + 0.9); return [Math.round(d[0] * 100) / 100, Math.round(d[1] * 100) / 100]; }
(function buildNav() {
  function line(x0, z0, x1, z1, pts) {
    var hor = Math.abs(z1 - z0) < 1e-6, a = Math.min(hor ? x0 : z0, hor ? x1 : z1), b = Math.max(hor ? x0 : z0, hor ? x1 : z1);
    var cs = [a, b].concat(pts || []).filter(function (c) { return c >= a - 1e-6 && c <= b + 1e-6; }).sort(function (p, q) { return p - q; }), last = null;
    cs.forEach(function (c) { if (last !== null && c - last < 0.05) return; if (last !== null) nLink(hor ? nAdd(last, z0) : nAdd(x0, last), hor ? nAdd(c, z0) : nAdd(x0, c)); last = c; });
  }
  var nN = [], nS = [-3.1], nW = [], nE = [];
  for (var key in ROOMS) { var R = ROOMS[key], dn = doorNode(R); R.node = dn; if (R.door === "S") nN.push(dn[0]); else if (R.door === "N") nS.push(dn[0]); else if (R.door === "E") nW.push(dn[1]); else nE.push(dn[1]); }
  line(-17.4, HALL_N, 17.4, HALL_N, [COR_W, COR_E, 0].concat(nN));
  line(-17.4, HALL_S, 17.4, HALL_S, [COR_W, COR_E, 0].concat(nS));
  line(COR_W, HALL_N, COR_W, HALL_S, [0].concat(nW));
  line(COR_E, HALL_N, COR_E, HALL_S, [0].concat(nE));
  /* 总经理办公室：南门进，门内一条横道通会议桌，大班台前一条短横道 */
  line(0, 0.4, 0, HALL_S, [3.2, 4.4]); line(0, 3.2, 5.0, 3.2, [2.15]); line(0, 0.4, 1.75, 0.4, []);
  /* 大堂 → 大门台阶 */
  line(0, HALL_S, 0, BZ1 + 0.8, [6.8, 12.0]);
  line(0, 6.8, 1.4, 6.8, []); line(1.4, 6.8, 1.4, 11.75, [7.4, 8.4, 9.9, 10.3]);
})();
var RCACHE = {};
function dijk(a, b) {
  var key = a * 1000 + b; if (RCACHE[key]) return RCACHE[key];
  var N = NODES.length, dist = [], prev = [], done = [], i;
  for (i = 0; i < N; i++) { dist[i] = 1e9; prev[i] = -1; done[i] = false; }
  dist[a] = 0;
  for (;;) {
    var u = -1, best = 1e9;
    for (i = 0; i < N; i++) if (!done[i] && dist[i] < best) { best = dist[i]; u = i; }
    if (u < 0 || u === b) break;
    done[u] = true;
    for (i = 0; i < NADJ[u].length; i++) { var e = NADJ[u][i], nd = dist[u] + e[1]; if (nd < dist[e[0]]) { dist[e[0]] = nd; prev[e[0]] = u; } }
  }
  var out = [], v = b; while (v >= 0) { out.unshift(v); v = prev[v]; }
  if (out[0] !== a) out = [a, b];
  RCACHE[key] = out; return out;
}
/* 位置（place）：x,z 站 / 坐的点；yaw 朝向；pose：sit / stand / bean；loc：走到图节点的折线；cell：所在房间 */
function mkPlace(x, z, yaw, pose, loc, nodeXZ, cell, inner, sh) {
  return { x: x, z: z, yaw: yaw, pose: pose, loc: loc, node: nAdd(nodeXZ[0], nodeXZ[1]), cell: cell === undefined ? "" : cell, inner: inner || null, sh: sh === undefined ? SEAT_H : sh, occ: null };
}
function rPlace(R, u, v, yawL, pose, lane) {
  var W = function (a, b) { return rw(R, a, b); }, pts = [W(u, v)];
  if (Math.abs(v - lane) > 0.05) pts.push(W(u, lane));
  pts.push(W(R.doorU, lane));
  var inner = pts.slice(); pts.push(W(R.doorU, R.hd));
  var p0 = W(u, v);
  return mkPlace(p0[0], p0[1], yawL + R.a, pose, pts, R.node, R.key, inner);
}
/* 每个部门：工位（座位 + 来访站位 + 桌上收件盘）、负责人座位 */
DEPTS.forEach(function (D, k) {
  var R = D.R; D.seats = [];
  R.cl.forEach(function (c) {
    for (var i = 0; i < 6; i++) {
      var north = i < 3, u = c[0] + (i % 3 - 1) * 1.35, v = c[1] + (north ? -1.0 : 1.0), lane = north ? c[1] - 1.88 : c[1] + 1.78;
      var T = TR(north ? 0 : Math.PI, u, c[1] + (north ? -0.36 : 0.36)), tr = tf(T, [1.25 / 2 - 0.2, DESK_Y + 0.02, -0.02]), dp = tf(T, [-0.33, DESK_Y + 0.002, -0.06]);
      var trw = rw(R, tr[0], tr[2]), dpw = rw(R, dp[0], dp[2]);
      D.seats.push({ place: rPlace(R, u, v, north ? 0 : Math.PI, "sit", lane), visit: rPlace(R, u + 0.5, lane, Math.atan2(-0.5, v - lane), "stand", lane),
        tray: [trw[0], tr[1], trw[1]], trayYaw: (north ? 0 : Math.PI) + R.a, deskPt: [dpw[0], dp[1], dpw[1]], own: null, head: false, D: k });
    }
  });
  /* 大房间有两组工位：交错排座，两组都有人 */
  if (R.cl.length > 1) { var a0 = D.seats.slice(0, 6), b0 = D.seats.slice(6), mix = []; for (var j = 0; j < 6; j++) mix.push(a0[j], b0[j]); D.seats = mix; }
  var hu = R.head[0], hv = R.head[1], TH = TR(0, hu, hv + 0.6), htr = tf(TH, [1.7 / 2 - 0.2, DESK_Y + 0.02, -0.02]), hdp = tf(TH, [0.3, DESK_Y + 0.002, -0.2]);
  var htw = rw(R, htr[0], htr[2]), hdw = rw(R, hdp[0], hdp[2]);
  D.headSeat = { place: rPlace(R, hu, hv, 0, "sit", hv), visit: rPlace(R, hu, R.cl[0][1] - 1.88, Math.PI, "stand", R.cl[0][1] - 1.88),
    tray: [htw[0], htr[1], htw[1]], trayYaw: R.a, deskPt: [hdw[0], hdp[1], hdw[1]], own: null, head: true, D: k };
  D.cap = D.seats.length;
  var cc = rw(R, 0, 0); D.cx = cc[0]; D.cz = cc[1];
});
/* Chief 坐在总经理办公室的老板椅上（坐北朝南）；来访的人站在大班台前、两把访客椅中间。
   Chief 模型放大 1.15 倍，座高按缩放折算，坐下后正好落在椅面上 */
var CHIEF_SEAT = { place: mkPlace(0, -3.0, 0, "sit", [[0, -3.0], [1.75, -3.0], [1.75, 0.4]], [1.75, 0.4], "", null, 0.26),
  visit: mkPlace(0, -0.9, Math.PI, "stand", [[0, -0.9], [0, 0.4]], [0, 0.4]),
  tray: [0.6, 0.62, HOLO[1] + 0.1], trayYaw: 0.25, deskPt: [-0.3, 0.61, HOLO[1] - 0.2], own: null, head: false, D: -1 };
var MEETSEATS = [], LOUNGE = [], GMSEATS = [];
/* 总经理办公室会议桌 6 座（Chief 召集的负责人周会用）：西侧 3 座面朝东，东侧 3 座面朝西 */
[0, -0.8, 0.8].forEach(function (dz) {
  var z = GM_TBL[1] + dz, xw = GM_TBL[0] - 0.85, xe = GM_TBL[0] + 0.85;
  GMSEATS.push(mkPlace(xw, z, Math.PI / 2, "sit", [[xw, z], [2.15, z], [2.15, 3.2]], [2.15, 3.2]));
  GMSEATS.push(mkPlace(xe, z, -Math.PI / 2, "sit", [[xe, z], [5.0, z], [5.0, 3.2]], [5.0, 3.2]));
});
[8.5, 9.35, 10.2].forEach(function (z) {
  MEETSEATS.push(mkPlace(-4.95, z, Math.PI / 2, "sit", [[-4.95, z], [-5.55, z], [-5.55, 6.9], [-3.1, 6.9], [-3.1, 6.2]], [-3.1, HALL_S]));
  MEETSEATS.push(mkPlace(-3.25, z, -Math.PI / 2, "sit", [[-3.25, z], [-2.65, z], [-2.65, 6.9], [-3.1, 6.9], [-3.1, 6.2]], [-3.1, HALL_S]));
});
LOUNGE.push(mkPlace(5.0, 7.4, Math.PI / 2, "stand", [[5.0, 7.4]], [1.4, 7.4]));
LOUNGE.push(mkPlace(5.0, 8.4, Math.PI / 2, "stand", [[5.0, 8.4]], [1.4, 8.4]));
LOUNGE.push(mkPlace(3.25, 11.85, Math.PI, "sit", [[3.25, 11.85]], [1.4, 11.75], "", null, 0.36));
LOUNGE.push(mkPlace(4.35, 11.85, Math.PI, "sit", [[4.35, 11.85], [4.35, 11.75]], [1.4, 11.75], "", null, 0.36));
LOUNGE.push(mkPlace(3.0, 9.9, 0.7, "bean", [[3.0, 9.9]], [1.4, 9.9], "", null, 0.2));
LOUNGE.push(mkPlace(4.6, 10.0, -0.75, "bean", [[4.6, 10.0], [4.6, 10.3]], [1.4, 10.3], "", null, 0.2));
var GATE = mkPlace(0, BZ1 + 0.8, Math.PI, "stand", [[0, BZ1 + 0.8]], [0, BZ1 + 0.8]);
function routeBetween(A, B) {
  if (A === B) return [[A.x, A.z], [A.x + 0.01, A.z]];
  if (A.cell && A.cell === B.cell && A.inner && B.inner) return A.inner.concat(B.inner.slice().reverse());
  var ns = dijk(A.node, B.node).map(function (i) { return NODES[i]; });
  return A.loc.concat(ns, B.loc.slice().reverse());
}

/* =====================================================================
   17. 成员：创建 / 安排座位 / 名单存取（名单存在本浏览器，可在人员管理里恢复默认）
   ===================================================================== */
var AIC = lin(hex3("#6fdcff")), REDL = lin([1.0, 0.32, 0.28]), OKL = lin([0.4, 0.92, 0.6]);
var PEOPLE = [], CHIEF = null, PID = 0, ROSTER_KEY = "agent-hq-roster-v2";
function living() { return PEOPLE.filter(function (p) { return !p.gone; }); }
function members(k) { return PEOPLE.filter(function (p) { return p.d === k && !p.gone; }); }
function headOf(k) { return DEPTS[k].headSeat.own; }
function colorize(p) { p.shL = lin(hex3(p.sc)); p.hcL = lin(hex3(p.hc)); p.dcL = lin(hex3(dColor(p.d))); p.hd = PH.headMap[p.skin] !== undefined ? PH.headMap[p.skin] : 0; }
function mkTag(p) {
  var d = document.createElement("div");
  d.className = "tg"; d.setAttribute("data-pid", p.id);
  d.innerHTML = '<div class="t1"><span class="bd"></span><b></b><em>—</em></div><div class="t2">—</div><div class="bar"><i></i></div>';
  $("tags").appendChild(d);
  p.tag = { el: d, bd: d.querySelector(".bd"), nm: d.querySelector(".t1 b"), st: d.querySelector(".t1 em"), t2: d.querySelector(".t2"), bar: d.querySelector(".bar i"), vis: false, tf: "", w: 120, h: 50 };
  tagSync(p);
}
function tagSync(p) { if (!p.tag) return; p.tag.el.setAttribute("style", "--ring:" + (p.ai ? "var(--ai)" : "var(--hu)")); setHTML(p.tag.bd, badge(p)); setTxt(p.tag.nm, p.n); }
function mkPerson(o) {
  var p = { id: ++PID, n: o.n, role: o.role, d: o.d, ai: !!o.ai, head: !!o.head, hs: o.hs, hc: o.hc, sc: o.sc, skin: o.skin, skills: (o.skills || []).slice(),
    q: [], cur: null, inb: 0, idle: rr(0, 4), idleMax: rr(6, 14), wait: 0, after: null, task: null, carry: null, ph: rr(0, 6), tok: 0, doneN: 0, revN: 0, rejN: 0, hist: [],
    spd: rr(2.2, 2.6), tokR: o.ai ? rr(380, 900) : 0, lean: 0, talkT: 0, sk: 1, gone: false, dead: false, seat: null, x: 0, z: 0, yaw: 0, yawT: 0, mode: "sit", pose: "sit", at: null };
  colorize(p); mkTag(p); PEOPLE.push(p); return p;
}
function takeSeat(p, s) {
  if (p.seat && p.seat.own === p) p.seat.own = null;
  s.own = p; p.seat = s; p.home = s.place; p.visit = s.visit; p.tray = s.tray; p.trayYaw = s.trayYaw; p.deskPt = s.deskPt;
  p.head = !!s.head; if (s.D >= 0) p.d = s.D;
  p.dcL = lin(hex3(dColor(p.d)));
}
function placeHome(p) { p.at = p.home; p.x = p.home.x; p.z = p.home.z; p.yaw = p.yawT = p.home.yaw; p.pose = p.home.pose; p.sk = p.pose === "stand" ? 0 : 1; p.mode = p.pose === "stand" ? "stand" : "sit"; }
function freeSeat(k, p) { var S = DEPTS[k].seats; for (var i = 0; i < S.length; i++) if (!S[i].own || S[i].own === p) return S[i]; return null; }
function seatCount(k) { var n = 0; DEPTS[k].seats.forEach(function (s) { if (s.own) n++; }); if (DEPTS[k].headSeat.own) n++; return n; }
/* 计划：把 p 安排到部门 k（wantHead = 是否当负责人）；返回 { seat, bump, bumpSeat } 或 { err } */
function planSeat(p, k, wantHead) {
  var D = DEPTS[k];
  if (wantHead) {
    var h = D.headSeat.own;
    if (!h || h === p) return { seat: D.headSeat };
    var f = null; for (var i = 0; i < D.seats.length; i++) { var s = D.seats[i]; if (!s.own || s.own === p) { f = s; break; } }
    if (!f) return { err: D.zh + "工位已满（" + D.cap + " 个），原负责人" + h.n + "没处坐。先移出一位成员再调整。" };
    return { seat: D.headSeat, bump: h, bumpSeat: f };
  }
  if (p && p.seat && p.d === k && !p.seat.head) return { seat: p.seat };
  var f2 = freeSeat(k, p);
  if (!f2) return { err: D.zh + "工位已满（" + D.cap + " 个），先移出一位成员，或换个部门。" };
  return { seat: f2 };
}
function applyPlan(p, pl) {
  if (pl.bump) { var h = pl.bump; if (h.seat === pl.seat) pl.seat.own = null; takeSeat(h, pl.bumpSeat); h.head = false; relocate(h); }
  if (pl.seat.own && pl.seat.own !== p) pl.seat.own = null;
  takeSeat(p, pl.seat);
}
/* 换了座位：在工位上就起身走过去，在外面的回来时自然走到新座位 */
function relocate(p) { if (p.gone || !p.at) return; if (p.mode !== "walk" && p.wait <= 0 && (p.at.cell || p.at === CHIEF_SEAT.place) && p.at !== p.home) { p.task = "home"; walkTo(p, p.home, null); } }
function rosterData() { return living().filter(function (p) { return p !== CHIEF; }).map(function (p) { return [p.n, p.role, p.d, p.ai ? 1 : 0, p.head ? 1 : 0, p.hs, p.hc, p.sc, p.skin, p.skills]; }); }
function saveRoster() { try { localStorage.setItem(ROSTER_KEY, JSON.stringify(rosterData())); } catch (e) { /* 浏览器不让存就算了 */ } }
function loadRoster() {
  try {
    var s = localStorage.getItem(ROSTER_KEY); if (!s) return null;
    var a = JSON.parse(s); if (!a || typeof a.length !== "number") return null;
    if (!a.length) return [];
    var ok = a.filter(function (r) { return r && typeof r[0] === "string" && r[0] && r[0].length <= 12 && typeof r[1] === "string" && r[2] === (r[2] | 0) && r[2] >= 0 && r[2] < ND && r[5] === (r[5] | 0) && r[5] >= 0 && r[5] < 5 && /^#[0-9a-f]{6}$/i.test(r[6]) && /^#[0-9a-f]{6}$/i.test(r[7]) && SKINS.indexOf(r[8]) >= 0; });
    ok.forEach(function (r) { var sk = r[9]; r[9] = (sk && typeof sk.length === "number" && typeof sk !== "string") ? [].slice.call(sk, 0, 5).filter(function (x) { return typeof x === "string"; }) : []; });
    return ok.length ? ok : null;
  } catch (e) { return null; }
}
function rowObj(r) { return { n: r[0], role: r[1], d: r[2], ai: !!r[3], head: !!r[4], hs: r[5], hc: r[6], sc: r[7], skin: r[8], skills: r[9] || [] }; }
function seatRoster(rows) {
  rows.forEach(function (r) {
    var o = rowObj(r), D = DEPTS[o.d];
    if (o.head && D.headSeat.own) o.head = false;
    var s = o.head ? D.headSeat : freeSeat(o.d, null);
    if (!s) return;
    var p = mkPerson(o); takeSeat(p, s); placeHome(p);
  });
}
(function initRoster() {
  var c = DEF_ROSTER[0];
  CHIEF = mkPerson(rowObj(c)); CHIEF.d = -1; CHIEF.isChief = true; takeSeat(CHIEF, CHIEF_SEAT); CHIEF.d = -1; CHIEF.dcL = lin(hex3(CHIEF_C)); placeHome(CHIEF);
  var saved = loadRoster();
  seatRoster(saved ? saved : DEF_ROSTER.slice(1));
})();

/* =====================================================================
   18. 项目：模板拆成若干事项；事项按阶段在部门之间传递
   ===================================================================== */
var PROJS = [], PJN = 0, PCI = 0;
function mkProject(o) {
  var t = TPL[o.tpl], S0 = o.S || t.S;
  var P = { id: "P" + (++PJN), zh: o.zh, c: o.c || PCOLS[PCI++ % PCOLS.length], tpl: o.tpl, S: S0.map(function (s) { return { d: s[0], zh: s[1], rv: s[2], t0: s[3], t1: s[4] }; }),
    T: o.T.slice(), items: [], done: 0, state: "run", doneAt: 0, newAt: now, phase: o.phase || 1, preset: !!o.preset, user: !!o.user, urgent: !!o.urgent, born: now, raw: S0 };
  P.ns = P.S.length; P.N = P.T.length; P.col = hex3(P.c); P.colL = lin(P.col); P.own = P.S[0].d;
  P.items = P.T.map(function (tt, k) { return { id: P.id + "-" + pad(k + 1, 2), P: P, k: k, t: tt, s: 0, st: "back", who: null, wk: null, prog: 0, dur: 1, rw: false, rwN: 0, born: now, kind: "work" }; });
  if (o.user) PROJS.unshift(P); else PROJS.push(P);
  return P;
}
function projOwner(P) { var d = P.own; if (d < 0) return CHIEF; return headOf(d) || CHIEF; }

/* =====================================================================
   19. 模拟：Chief 派单 → 各部门 Agent 按阶段处理 → 负责人（真人督办 / Agent）审核 → 走过去或「飞」给下一棒 → 回到 Chief 归档
   ===================================================================== */
var ST = { done: 0, stages: 0, rev: 0, rej: 0, tok: 0, tokRate: 0, lead: [], dispT: 1.2, dispN: 0, meetT: 16, patT: 75, hired: 0, left: 0 };
var FLYS = [], RINGS = [], MT = null, TIMERS = [];
function wipOf(P) { return Math.max(5, Math.round(P.N * 0.8)) + (P.urgent ? 2 : 0); }
function load(p) { return p.q.length + (p.cur ? 1 : 0) + p.inb + (p.at !== p.home ? 0.6 : 0); }
function stageOf(it) { return it.P.S[Math.min(it.s, it.P.ns - 1)]; }
function stageNm(it) { return stageOf(it).zh; }
function rvOf(S) {
  if (S.rv === 2 || S.d < 0) return CHIEF;
  if (S.rv !== 1) return null;
  var h = headOf(S.d); if (h && !h.gone) return h;
  var hu = members(S.d).filter(function (p) { return !p.ai; }); return hu.length ? hu[0] : CHIEF;
}
function pickWorker(S, excl) {
  if (S.d < 0) return CHIEF;
  var best = null, bs = 1e9, rv = rvOf(S);
  members(S.d).forEach(function (p) { if (!p.ai || p === rv || p === excl) return; var sc = load(p) + (p.head ? 0.8 : 0) + Math.random() * 0.4; if (sc < bs) { bs = sc; best = p; } });
  return best || CHIEF;
}
function walkersN() { var n = 0; PEOPLE.forEach(function (p) { if (p.task === "deliver") n++; }); return n; }
function tagOf(it, ps) { var g = { j: it.P.id, d: [], p: [] }; it.P.S.forEach(function (s) { if (s.d >= 0 && g.d.indexOf(s.d) < 0) g.d.push(s.d); }); (ps || []).forEach(function (p) { if (p) { g.p.push(p.id); if (p.d >= 0 && g.d.indexOf(p.d) < 0) g.d.push(p.d); } }); return g; }
function hist(p, s) { p.hist.unshift({ t: hms().slice(0, 5), s: s }); if (p.hist.length > 12) p.hist.length = 12; }
function pcol(p) { return p.isChief ? CHIEF_C : (p.ai ? dColor(p.d) : "#ffd060"); }
function sendTo(it, from, to, kind) {
  it.kind = kind; it.who = to; to.inb++;
  if (from === to) { arrive(it); return; }
  var a = from.home, b = to.home, d = hyp(a.x - b.x, a.z - b.z);
  var canWalk = from !== CHIEF && !from.gone && from.at === from.home && from.mode !== "walk" && from.wait <= 0 && !from.carry && to.at === to.home && !to.gone &&
    to.visit.occ === null && d > 1.2 && d < 10.5 && walkersN() < 4 && Math.random() < (from.ai ? 0.72 : 0.22);
  if (canWalk) {
    from.carry = it; it.st = "carry"; from.task = "deliver";
    walkTo(from, to.visit, function () {
      from.wait = 1.1; from.yawT = Math.atan2(to.x - from.x, to.z - from.z);
      say(from, rpick(["交给你了 · ", "这个给你 · ", "下一棒 · "]) + it.t, "ai", 1);
      from.after = function () { from.carry = null; arrive(it); if (!to.gone) say(to, rpick(["收到！", "好，马上看", "OK 收到"]), to.ai ? "ai" : "", 0); from.task = "home"; walkTo(from, from.home, null); };
    });
    return;
  }
  flyItem(it, (from.at === from.home || from === CHIEF) ? from.tray.slice() : [from.x, 1.0, from.z], to.tray, d);
}
function flyItem(it, fa, fb, d) { it.st = "fly"; it.fa = fa; it.fb = fb; it.ft = 0; d = d === undefined ? hyp(fa[0] - fb[0], fa[2] - fb[2]) : d; it.fd = clamp(0.7 + d * 0.06, 0.8, 2.2); it.fh = 0.7 + d * 0.09; FLYS.push(it); }
function reroute(it, fromPos) {
  var S = stageOf(it), to;
  if (it.kind === "fin") to = CHIEF; else if (it.kind === "rev") to = rvOf(S) || CHIEF; else to = pickWorker(S, null);
  it.who = to; to.inb++;
  flyItem(it, fromPos || [0, 0.8, 0], to.tray);
}
function arrive(it) {
  var to = it.who;
  if (!to) { it.who = CHIEF; to = CHIEF; }
  to.inb = Math.max(0, to.inb - 1);
  if (to.gone) { reroute(it, to.tray.slice()); return; }
  if (it.kind === "fin") { complete(it); return; }
  /* 送达时对方已经调走 / 改成真人督办：换人接手（干活只给本部门 Agent，审核只给本部门的人或 Chief） */
  var S = stageOf(it);
  if (to !== CHIEF && S.d >= 0 && (to.d !== S.d || (it.kind === "work" && !to.ai))) { reroute(it, to.tray.slice()); return; }
  it.st = "q"; to.q.push(it);
  /* 加急 > 你派的 > 示例项目；同级保持先来后到 */
  if (it.P.urgent || it.P.user) to.q.sort(function (a, b) { return qPri(b) - qPri(a); });
}
function qPri(x) { return (x.P.urgent ? 2 : 0) + (x.P.user ? 1 : 0); }
function startTask(p) {
  var it = p.q.shift(); p.cur = it;
  var S = stageOf(it);
  if (it.kind === "rev") { it.st = "rev"; it.dur = S.d === -1 ? rr(S.t0, S.t1) : (p.ai ? rr(1.8, 2.8) : rr(2.8, 4.4)); if (!p.ai && Math.random() < 0.45) say(p, rpick(["我看看…", "嗯，过一下", "看下细节"]), "", 0); }
  else { it.st = "work"; it.dur = rr(S.t0, S.t1) * 0.85 * (it.rw ? 0.55 : 1) * (p === CHIEF ? 1.4 : 1) * (it.P.urgent ? 0.75 : (it.P.user ? 0.85 : 1)); }
  it.prog = 0;
}
function finishTask(p, it) {
  p.cur = null; it.prog = 1;
  var P = it.P, S = stageOf(it);
  if (it.st === "work") {
    p.doneN++; ST.stages++; it.wk = p;
    hist(p, "完成 " + P.id + " · " + it.t + " · " + S.zh);
    var rv = rvOf(S);
    if (rv && rv !== p) { log(p.n, pcol(p), P.id + " · " + it.t + " · " + S.zh + "完成 → 交 " + rv.n + " 审核", "", tagOf(it, [p, rv])); sendTo(it, p, rv, "rev"); }
    else advance(it, p);
  } else {
    ST.rev++; p.revN++;
    var pass = Math.random() < (p === CHIEF ? 0.88 : (p.ai ? 0.92 : 0.85));
    if (it.rwN >= 2) pass = true;
    if (pass) {
      hist(p, "审核通过 " + P.id + " · " + it.t);
      say(p, rpick(["通过 ✓", "可以，继续", "没问题，下一步", "不错，过了"]), p.ai ? "ai" : "g", 1);
      log(p.n, pcol(p), "审核通过 · " + P.id + " " + it.t + " · " + S.zh, "g", tagOf(it, [p]));
      advance(it, p);
    } else {
      ST.rej++; p.rejN++; it.rw = true; it.rwN++;
      hist(p, "退回返工 " + P.id + " · " + it.t);
      say(p, rpick(["这里再改改", "细节不够，退回", "口径不对，重做", "再打磨一下"]), "r", 2);
      if (S.d === -1) it.s = Math.max(0, it.s - 1);
      var back = it.wk && !it.wk.gone && it.wk.ai && it.wk.d === stageOf(it).d ? it.wk : pickWorker(stageOf(it), null);
      log(p.n, pcol(p), "退回返工 · " + P.id + " " + it.t + " → " + back.n, "r", tagOf(it, [p, back]));
      RINGS.push({ p: [p.x, 0.04, p.z], c: [1, 0.35, 0.28], t0: now, r: 1.2 });
      sendTo(it, p, back, "work");
    }
  }
}
function advance(it, from) {
  var P = it.P;
  it.rw = false; it.rwN = 0; it.s++;
  if (it.s >= P.ns) { it.kind = "fin"; it.who = CHIEF; CHIEF.inb++; flyItem(it, from.at === from.home ? from.tray.slice() : [from.x, 1.0, from.z], CHIEF.tray); it.fd = 2.0; it.fh = 3.0; return; }
  var S = P.S[it.s], to = S.d === -1 ? CHIEF : pickWorker(S, null);
  if (S.d === -1) log(from.n, pcol(from), P.id + " · " + it.t + " → 交 Chief " + S.zh, "", tagOf(it, [from, CHIEF]));
  else if (to.d !== from.d || Math.random() < 0.3) log(from.n, pcol(from), P.id + " · " + it.t + " → " + to.n + "（" + S.zh + "）", "", tagOf(it, [from, to]));
  sendTo(it, from, to, S.d === -1 ? "rev" : "work");
}
function complete(it) {
  var P = it.P;
  it.st = "fin"; it.s = P.ns; it.finAt = now; P.done++; ST.done++; ST.lead.push(now - it.born); if (ST.lead.length > 30) ST.lead.shift();
  log("小枢", CHIEF_C, "交付归档 · " + P.id + " " + it.t + "（" + P.done + "/" + P.N + "）", "g", tagOf(it, [CHIEF]));
  RINGS.push({ p: [HOLO[0], 0.7, HOLO[1]], c: P.col, t0: now, r: 1.6 });
  if (Math.random() < 0.3) say(CHIEF, "归档 · " + P.id + " " + it.t, "ai", 0);
  if (P.done >= P.N && P.state === "run") {
    P.state = "done"; P.doneAt = now;
    var ow = projOwner(P);
    log("小枢", CHIEF_C, P.id + " " + P.zh + (P.preset ? " · " + PHASE_NM[(P.phase - 1) % 9] : "") + " 全部交付！", "g", { j: P.id, d: [], p: [CHIEF.id, ow.id] });
    say(CHIEF, P.zh + " 全部交付！", "g", 3);
    for (var i = 0; i < 3; i++) RINGS.push({ p: [HOLO[0], 0.7 + i * 0.5, HOLO[1]], c: P.col, t0: now + i * 0.25, r: 2.6 });
  }
}
function nextPhase(P) {
  P.phase++; P.state = "run"; P.newAt = now; P.done = 0;
  P.items = shuffle(P.T.slice()).map(function (tt, k) { return { id: P.id + "-" + pad(k + 1, 2), P: P, k: k, t: tt, s: 0, st: "back", who: null, wk: null, prog: 0, dur: 1, rw: false, rwN: 0, born: now, kind: "work" }; });
  log("小枢", CHIEF_C, P.id + " " + P.zh + " · 启动" + PHASE_NM[(P.phase - 1) % 9] + " · " + P.N + " 项排期", "", { j: P.id, d: [], p: [CHIEF.id] });
}
function dispatch() {
  var best = null, bs = 1e9;
  PROJS.forEach(function (P) {
    if (P.state !== "run") return;
    var act = 0, back = 0; P.items.forEach(function (it) { if (it.st === "back") back++; else if (it.st !== "fin") act++; });
    if (!back || act >= wipOf(P)) return;
    var sc = act / wipOf(P) + Math.random() * 0.5 - (P.urgent ? 3 : 0) - (P.user ? 0.6 : 0); if (sc < bs) { bs = sc; best = P; }
  });
  if (!best) return false;
  var it = null; for (var i = 0; i < best.items.length; i++) if (best.items[i].st === "back") { it = best.items[i]; break; }
  if (!it) return false;
  var S = best.S[0], to = S.d === -1 ? CHIEF : pickWorker(S, null);
  it.born = now; ST.dispN++;
  log("小枢", CHIEF_C, "派单 · " + best.id + " " + it.t + " → " + to.n, "", tagOf(it, [CHIEF, to]));
  if (ST.dispN % 4 === 1) say(CHIEF, "派单 → " + to.n, "ai", 0);
  CHIEF.talkT = 0.8;
  sendTo(it, CHIEF, to, S.d === -1 ? "rev" : "work");
  return true;
}
/* 走路：先起身，再沿路线走；到了按位置坐下 / 站着 */
function walkTo(p, place, cb) {
  var from = p.at || p.home;
  if (from && from !== place && from.occ === p) from.occ = null;
  var pts = routeBetween(from, place);
  if (Math.abs(pts[0][0] - p.x) > 0.05 || Math.abs(pts[0][1] - p.z) > 0.05) pts.unshift([p.x, p.z]);
  p.wp = pathMake(pts, 0.32); p.ws = 0; p.mode = "walk"; p.dest = place; p.cb = cb; p.from = from; p.at = null;
  if (place !== p.home && !place.occ) place.occ = p;
}
function stepWalk(p, dt) {
  if (p.sk > 0.02) { p.sk = Math.max(0, p.sk - dt * 4.5); return; }
  p.ws += dt * p.spd; p.ph += dt * p.spd * 5.2;
  var q = pathAt(p.wp, p.ws); p.x = q.x; p.z = q.z; p.yawT = Math.atan2(q.dx, q.dz);
  if (p.ws >= p.wp.len) {
    var pl = p.dest; p.at = pl; p.x = pl.x; p.z = pl.z; p.yawT = pl.yaw; p.pose = pl.pose;
    p.mode = pl.pose === "stand" ? "stand" : "sit"; p.wp = null;
    if (pl === p.home) { p.task = null; p.idle = 0; }
    var cb = p.cb; p.cb = null; if (cb) cb();
  }
}
function goHome(p) { p.task = "home"; if (p.at && p.at !== p.home && p.at.occ === p) p.at.occ = null; walkTo(p, p.home, null); }
function updPerson(p, dt) {
  if (p.mode !== "walk") p.sk = p.mode === "sit" ? Math.min(1, p.sk + dt * 3.5) : Math.max(0, p.sk - dt * 4.5);
  p.yaw += angDiff(p.yaw, p.yawT) * Math.min(1, dt * 8);
  if (p.talkT > 0) p.talkT -= dt;
  if (p.mode === "walk") { stepWalk(p, dt); return; }
  if (p.gone) return;
  if (p.wait > 0) { p.wait -= dt; if (p.wait <= 0 && p.after) { var f = p.after; p.after = null; f(); } return; }
  if (p.at === p.home) {
    if (!p.cur && p.q.length) startTask(p);
    if (p.cur) {
      var it = p.cur; it.prog += dt / it.dur;
      if (p.ai && it.st === "work") { var tk = dt * p.tokR; p.tok += tk; ST.tok += tk; }
      if (it.prog >= 1) finishTask(p, it);
      p.idle = 0;
    } else if (p !== CHIEF) { p.idle += dt; if (p.idle > p.idleMax) maybeBreak(p); }
  } else if (p.task === "lounge") {
    p.lt -= dt;
    if (p.lt <= 0 || ((p.q.length || p.inb) && p.lt0 - p.lt > 2.5)) goHome(p);
  } else if ((!p.task || p.task === "home") && p.at !== p.home) goHome(p);
}
function maybeBreak(p) {
  p.idle = 0; p.idleMax = rr(7, 16);
  var nb = 0; PEOPLE.forEach(function (x) { if (x.task === "lounge") nb++; });
  if (nb >= 3 || Math.random() < (p.ai ? 0.45 : 0.6)) return;
  var free = LOUNGE.filter(function (s) { return !s.occ; }); if (!free.length) return;
  var spot = rpick(free);
  p.task = "lounge"; p.lt0 = p.lt = rr(6, 11);
  walkTo(p, spot, function () { say(p, rpick(["续杯咖啡", "伸个懒腰", "休息两分钟", "眼睛放松一下", "去茶水间坐坐"]), p.ai ? "ai" : "", 0); });
}
/* 会议：围绕某个在推进的项目，牵头部门负责人 + 相关部门的人去大堂会议室；
   偶尔 Chief 召集各部门负责人，在总经理办公室的会议桌开周会 */
function meetTick(dt) {
  if (!MT) {
    ST.meetT -= dt; if (ST.meetT > 0) return;
    var ppl = [], title = "", P = null, hostP = null, ok = function (p) { return p && !p.gone && p.at === p.home && p.mode !== "walk" && p.wait <= 0 && !p.task && ppl.indexOf(p) < 0; };
    if (Math.random() < 0.22 && ok(CHIEF) && !CHIEF.q.length && !CHIEF.cur) {
      title = "负责人周会"; hostP = CHIEF; ppl.push(CHIEF);
      DEPTS.forEach(function (D, k) { var h = headOf(k); if (ppl.length < 6 && ok(h)) ppl.push(h); });
    } else {
      var act = PROJS.filter(function (x) { return x.state === "run"; }); if (!act.length) { ST.meetT = 10; return; }
      P = rpick(act); title = P.zh + " · " + rpick(["评审会", "对齐会", "排期会", "进度会"]);
      P.S.forEach(function (S) { if (ppl.length < 6 && S.d >= 0 && ok(headOf(S.d))) ppl.push(headOf(S.d)); });
      PEOPLE.forEach(function (p) { if (ppl.length < 5 && ok(p) && p.ai && p !== CHIEF && holds(p, P)) ppl.push(p); });
      hostP = ppl[0];
    }
    if (ppl.length < 3) { ST.meetT = 8; return; }
    var gm = hostP === CHIEF;
    MT = { title: title, P: P, ppl: ppl, ph: "go", t0: now, dur: rr(14, 20), arr: 0, chat: 3, loc: gm ? "gm" : "meet", seats: gm ? GMSEATS : MEETSEATS, c: gm ? GM_TBL : MEET_C };
    ppl.forEach(function (p, i) { var st = MT.seats[i]; st.occ = p; p.task = "meet"; walkTo(p, st, function () { if (MT) MT.arr++; }); });
    log(hostP.n, pcol(hostP), "发起会议 · " + title + "（" + (gm ? "总经理办公室" : "会议室") + " · " + ppl.length + " 人）", "", { j: P ? P.id : "", d: [], p: ppl.map(function (p) { return p.id; }) });
    return;
  }
  MT.ppl = MT.ppl.filter(function (p) { return !p.gone; });
  if (MT.ph === "go") {
    if (MT.arr >= MT.ppl.length || now - MT.t0 > 25) { MT.ph = "on"; MT.t1 = now; if (MT.ppl[0]) say(MT.ppl[0], "开始吧：" + MT.title, MT.ppl[0].ai ? "ai" : "", 2); }
    return;
  }
  MT.chat -= dt;
  if (MT.chat <= 0 && MT.ppl.length) { MT.chat = rr(2.4, 3.6); var sp = rpick(MT.ppl); if (sp.at && sp.mode === "sit") { say(sp, rpick(["我这边进度 " + ri(55, 90) + "%", "接口下午能好", "视觉稿已更新", "这个先排下一期", "同意这个方案", "预算在范围内", "风险点我记一下", "周五前能交付"]), sp.ai ? "ai" : "", 0); sp.talkT = 1.4; } }
  if (now - MT.t1 > MT.dur || !MT.ppl.length) {
    log(MT.ppl[0] ? MT.ppl[0].n : "小枢", MT.ppl[0] ? pcol(MT.ppl[0]) : CHIEF_C, "会议结束 · " + MT.title + " · 形成 " + ri(2, 5) + " 条待办", "", { j: MT.P ? MT.P.id : "", d: [], p: MT.ppl.map(function (p) { return p.id; }) });
    MT.ppl.forEach(function (p) { if (p.task !== "meet") return; if (p.mode === "walk") p.cb = function () { goHome(p); }; else goHome(p); });
    MT.seats.forEach(function (s) { s.occ = null; });
    MT = null; ST.meetT = rr(38, 62);
  }
}
/* Chief 巡视：走到某个部门负责人桌前问进度 */
function patrolTick(dt) {
  var c = CHIEF;
  ST.patT -= dt; if (ST.patT > 0) return;
  if (c.at !== c.home || c.cur || c.q.length || c.task || c.mode === "walk") { ST.patT = 6; return; }
  ST.patT = rr(85, 130);
  var ks = []; DEPTS.forEach(function (D, k) { if (headOf(k)) ks.push(k); }); if (!ks.length) return;
  var k = rpick(ks), D = DEPTS[k], hd = headOf(k);
  c.task = "patrol";
  log("小枢", CHIEF_C, "巡视 " + D.zh, "", { d: [k], p: [c.id, hd.id] });
  walkTo(c, D.headSeat.visit, function () {
    c.wait = 3.6; c.yawT = D.headSeat.visit.yaw;
    say(c, rpick(["进度怎么样？", "这周能按时吗？", "有卡点吗？"]), "ai", 2);
    setTimeoutSim(1.4, function () { if (hd.gone) return; var pc = Math.round(deptProg(k) * 100); say(hd, rpick(["按计划推进，" + pc + "%", "没问题，" + pc + "% 了", "有个依赖在跟，整体 " + pc + "%"]), hd.ai ? "ai" : "", 2); hd.talkT = 1.5; });
    c.after = function () { goHome(c); };
  });
}
function setTimeoutSim(sec, f) { TIMERS.push({ t: now + sec, f: f }); }
function updateSim(dt) {
  var i;
  for (i = TIMERS.length - 1; i >= 0; i--) if (now >= TIMERS[i].t) { var f = TIMERS[i].f; TIMERS.splice(i, 1); f(); }
  ST.dispT -= dt;
  if (ST.dispT <= 0) { ST.dispT = dispatch() ? rr(1.0, 1.9) : 0.8; }
  for (i = PROJS.length - 1; i >= 0; i--) { var P = PROJS[i]; if (P.state === "done") { if (P.preset && now - P.doneAt > 24) nextPhase(P); else if (!P.preset && now - P.doneAt > 90) { PROJS.splice(i, 1); projDirty = true; } } }
  for (i = RINGS.length - 1; i >= 0; i--) if (now - RINGS[i].t0 > 1.4) RINGS.splice(i, 1);
  for (i = FLYS.length - 1; i >= 0; i--) { var it = FLYS[i]; it.ft += dt / it.fd; if (it.ft >= 1) { FLYS.splice(i, 1); arrive(it); } }
  for (i = 0; i < PEOPLE.length; i++) updPerson(PEOPLE[i], dt);
  for (i = PEOPLE.length - 1; i >= 0; i--) if (PEOPLE[i].dead) { var dp = PEOPLE[i]; if (dp.tag && dp.tag.el.parentNode) dp.tag.el.parentNode.removeChild(dp.tag.el); PEOPLE.splice(i, 1); rosterDirty = true; }
  meetTick(dt); patrolTick(dt);
  var tr = 0; PEOPLE.forEach(function (p) { if (p.ai && p.cur && p.cur.st === "work" && p.at === p.home) tr += p.tokR; });
  ST.tokRate += (tr - ST.tokRate) * Math.min(1, dt * 2);
}
var projDirty = true, rosterDirty = true;

/* ---------- 人事：新增 / 修改 / 移出（离职的人先把手上工作移交，再从大门走出去） ---------- */
function nameTaken(n, except) { for (var i = 0; i < PEOPLE.length; i++) { var p = PEOPLE[i]; if (!p.gone && p !== except && p.n === n) return true; } return false; }
function randomLook() { return { hs: Math.floor(Math.random() * 5), hc: rpick(HAIRS), sc: rpick(SHIRTS), skin: rpick(SKINS) }; }
function hrAdd(o) {
  if (!o.n) return "请填写姓名";
  if (nameTaken(o.n)) return "已经有一位叫「" + o.n + "」的成员了，换个名字";
  var pl = planSeat(null, o.d, o.head); if (pl.err) return pl.err;
  var p = mkPerson(o);
  p.at = GATE; p.x = GATE.x; p.z = GATE.z; p.yaw = p.yawT = Math.PI; p.pose = "stand"; p.mode = "stand"; p.sk = 0;
  applyPlan(p, pl);
  p.task = "home"; walkTo(p, p.home, function () { say(p, "大家好，我是" + p.n + "！", p.ai ? "ai" : "g", 2); });
  ST.hired++;
  var hrh = headOf(HRD) || CHIEF;
  log(hrh.n, pcol(hrh), "新成员入职 · " + p.n + "（" + (p.ai ? "Agent" : "真人督办") + "）→ " + DEPTS[p.d].zh + (p.head ? " 负责人" : ""), "g", { d: [HRD, p.d], p: [p.id, hrh.id] });
  if (!hrh.gone && hrh !== CHIEF) say(hrh, "欢迎 " + p.n + " 加入" + DEPTS[p.d].zh, hrh.ai ? "ai" : "g", 1);
  saveRoster(); rosterDirty = true;
  return null;
}
function hrEdit(p, o) {
  if (p.gone) return "这位成员已经离开";
  if (!o.n) return "请填写姓名";
  if (nameTaken(o.n, p)) return "已经有一位叫「" + o.n + "」的成员了，换个名字";
  var moveDept = o.d !== p.d, headChange = !!o.head !== !!p.head, notes = [];
  if (moveDept || headChange) {
    var pl = planSeat(p, o.d, o.head); if (pl.err) return pl.err;
    var oldD = p.d, pos0 = p.tray.slice(), items = [];
    if (moveDept) { items = p.q.splice(0); if (p.cur) { p.cur.st = "q"; items.unshift(p.cur); p.cur = null; } }
    applyPlan(p, pl);
    /* 先换好座位再移交：这样不会又派回给自己 */
    items.forEach(function (it) { reroute(it, pos0); }); if (items.length) notes.push(items.length + " 项工作移交");
    if (pl.bump) notes.push(pl.bump.n + " 改为普通成员");
    relocate(p);
    if (moveDept) notes.unshift("从" + DEPTS[oldD].zh + "调到" + DEPTS[p.d].zh);
  }
  var wasAi = p.ai;
  p.n = o.n; p.role = o.role; p.skills = o.skills; p.ai = !!o.ai; p.hs = o.hs; p.hc = o.hc; p.sc = o.sc; p.skin = o.skin;
  if (wasAi !== p.ai) { notes.push(p.ai ? "改为 Agent" : "改为真人督办"); p.tokR = p.ai ? rr(380, 900) : 0; if (!p.ai) { var its = p.q.filter(function (it) { return it.kind === "work"; }); p.q = p.q.filter(function (it) { return it.kind !== "work"; }); if (p.cur && p.cur.st === "work") { its.unshift(p.cur); p.cur.st = "q"; p.cur = null; } its.forEach(function (it) { reroute(it, p.tray.slice()); }); } }
  colorize(p); tagSync(p);
  var hrh = headOf(HRD) || CHIEF;
  log(hrh.n, pcol(hrh), "更新成员 · " + p.n + (notes.length ? " · " + notes.join("，") : ""), "", { d: [HRD, p.d], p: [p.id, hrh.id] });
  saveRoster(); rosterDirty = true;
  return null;
}
function handover(p) { var items = p.q.splice(0); if (p.cur) { p.cur.st = "q"; items.unshift(p.cur); p.cur = null; } if (p.carry) { var c = p.carry; if (c.who) c.who.inb = Math.max(0, c.who.inb - 1); items.push(c); p.carry = null; p.task = null; } return items; }
function hrRemove(p) {
  if (p === CHIEF) return "Chief 不能移出";
  if (p.gone) return "这位成员已经在离开了";
  p.gone = true;
  if (p.seat && p.seat.own === p) p.seat.own = null;
  var D = p.d >= 0 ? DEPTS[p.d] : null, items = handover(p);
  p.after = null; p.wait = 0;
  var pos = p.tray.slice();
  items.forEach(function (it) { reroute(it, pos); });
  if (MT) MT.ppl = MT.ppl.filter(function (x) { return x !== p; });
  var hrh = headOf(HRD) || CHIEF;
  log(hrh.n, pcol(hrh), p.n + " 离开" + (D ? D.zh : "") + (items.length ? " · " + items.length + " 项工作已移交" : ""), "w", { d: [HRD, p.d], p: [p.id, hrh.id] });
  say(p, rpick(["大家再见！", "交接完毕，再见", "有缘再会"]), p.ai ? "ai" : "", 2);
  var leave = function () { p.task = "leave"; walkTo(p, GATE, function () { p.dead = true; }); };
  if (p.mode === "walk") p.cb = leave; else leave();
  ST.left++; saveRoster(); rosterDirty = true;
  return null;
}
function hrReset() {
  var moved = [];
  PEOPLE.forEach(function (p) { if (p === CHIEF || p.gone) return; moved = moved.concat(handover(p)); p.gone = true; p.dead = true; if (p.seat) p.seat.own = null; });
  for (var i = PEOPLE.length - 1; i >= 0; i--) if (PEOPLE[i].dead) { if (PEOPLE[i].tag && PEOPLE[i].tag.el.parentNode) PEOPLE[i].tag.el.parentNode.removeChild(PEOPLE[i].tag.el); PEOPLE.splice(i, 1); }
  if (MT) { MEETSEATS.concat(GMSEATS).forEach(function (s) { s.occ = null; }); MT = null; ST.meetT = 20; }
  LOUNGE.forEach(function (s) { s.occ = null; });
  /* 被清掉的人预约过的位置（送文件的来访站位等）一并释放 */
  DEPTS.forEach(function (D) { D.seats.concat([D.headSeat]).forEach(function (s) { if (s.visit.occ && s.visit.occ.dead) s.visit.occ = null; if (s.place.occ && s.place.occ.dead) s.place.occ = null; }); });
  if (CHIEF_SEAT.visit.occ && CHIEF_SEAT.visit.occ.dead) CHIEF_SEAT.visit.occ = null;
  /* Chief 若正在开会 / 去开会，散会回大班台 */
  if (CHIEF.task === "meet") { if (CHIEF.mode === "walk") CHIEF.cb = function () { goHome(CHIEF); }; else goHome(CHIEF); }
  PEOPLE.forEach(function (p) { p.inb = 0; });
  seatRoster(DEF_ROSTER.slice(1));
  FLYS.forEach(function (it) { if (it.who) it.who.inb++; });
  moved.forEach(function (it) { reroute(it, [0, 0.8, 0]); });
  log("小枢", CHIEF_C, "名单恢复默认 · " + (living().length) + " 人", "", { d: [HRD], p: [CHIEF.id] });
  try { localStorage.removeItem(ROSTER_KEY); } catch (e) { /* 忽略 */ }
  rosterDirty = true;
}

/* ---------- 统计 ---------- */
function itemUnits(it, P) { if (it.st === "fin") return P.ns; var u = Math.min(it.s, P.ns); if (it.st === "work") u += it.prog * 0.9; else if (it.st === "rev") u += 0.9 + it.prog * 0.1; else if (it.kind === "rev" && it.st !== "back") u += 0.9; return u; }
function projProg(P) { var s = 0; P.items.forEach(function (it) { s += itemUnits(it, P); }); return s / (P.N * P.ns); }
function deptProg(k) {
  var tot = 0, dn = 0;
  PROJS.forEach(function (P) {
    P.S.forEach(function (S, si) {
      if (S.d !== k) return;
      P.items.forEach(function (it) {
        tot++;
        if (it.st === "fin" || it.s > si) dn++;
        else if (it.s === si) { if (it.st === "work") dn += it.prog * 0.9; else if (it.st === "rev") dn += 0.9 + 0.1 * it.prog; else if (it.kind === "rev" && it.st !== "back") dn += 0.9; }
      });
    });
  });
  return tot ? dn / tot : 1;
}
function companyProg() { var a = 0, b = 0; PROJS.forEach(function (P) { var u = P.N * P.ns; a += projProg(P) * u; b += u; }); return b ? a / b : 1; }
function holds(p, P) { if (p.cur && p.cur.P === P) return true; if (p.carry && p.carry.P === P) return true; for (var i = 0; i < p.q.length; i++) if (p.q[i].P === P) return true; return false; }

/* =====================================================================
   20. 视图 + 镜头：全景 / 部门 / 项目 / 人员；切换时沿弧线飞过去；视角：全景 / 巡游 / 俯视
   ===================================================================== */
var V = { k: "ov", d: 0, P: null, p: null, back: null }, view = 0, FLY = null;
var VIEWS = ["全景", "巡游", "俯视"];
var CV = { t: [0.3, 0, 0.9], yaw: 0.28, pitch: 0.95, dist: 60 }, DRG = { yaw: 0, pitch: 0, zoom: 1 };
function stagePt(S) { return S.d >= 0 ? [DEPTS[S.d].cx, 0, DEPTS[S.d].cz] : [HOLO[0], 0, HOLO[1]]; }
function projCenter(P) { var x0 = 1e9, x1 = -1e9, z0 = 1e9, z1 = -1e9; P.S.forEach(function (S) { var c = stagePt(S); x0 = Math.min(x0, c[0]); x1 = Math.max(x1, c[0]); z0 = Math.min(z0, c[2]); z1 = Math.max(z1, c[2]); }); return [(x0 + x1) / 2, (z0 + z1) / 2]; }
function camGoal() {
  var n = L.narrow, top = view === 2;
  if (V.k === "ps" && V.p) { var p = V.p, ch = p.isChief && p.at === p.home, gy = groundY(p.x, p.z); return { t: [p.x, gy + (ch ? 0.75 : 0.62), p.z], yaw: top ? 0 : (ch ? 0.3 : (n ? 0.35 : 0.45)), pitch: top ? 1.3 : (ch ? 0.66 : 0.56), dist: (n ? 9.5 : 7.0) * (ch ? 1.18 : 1) }; }
  if (V.k === "dp") { var D = DEPTS[V.d], R = D.R, ext = Math.max(R.hw, R.hd); return { t: [D.cx, 0.2, D.cz + 0.3], yaw: top ? 0 : 0.3, pitch: top ? 1.38 : 0.86, dist: (n ? 12 : 9) + ext * (n ? 2.4 : 2.2) }; }
  if (V.k === "pj" && V.P) { var c = projCenter(V.P); return { t: [c[0] * 0.35, 0, c[1] * 0.35 + 1.4], yaw: top ? (n ? Math.PI / 2 : 0) : (n ? 1.4 : 0.24), pitch: top ? 1.42 : 0.98, dist: n ? 78 : 71 }; }
  if (top) return n ? { t: [0, 0, 0], yaw: Math.PI / 2, pitch: 1.42, dist: 70 } : { t: [0, 0, 0.4], yaw: 0, pitch: 1.42, dist: 63 };
  return n ? { t: [0, 0, 0], yaw: 1.4, pitch: 1.05, dist: 78 } : { t: [0, 0, 1.5], yaw: 0.24, pitch: 0.95, dist: 69 };
}
function snapCam() { var g = camGoal(); CV = { t: g.t.slice(), yaw: g.yaw, pitch: g.pitch, dist: g.dist }; FLY = null; }
function startFly(hop) {
  var a = { t: CV.t.slice(), yaw: CV.yaw + DRG.yaw, pitch: clamp(CV.pitch + DRG.pitch, 0.06, 1.45), dist: CV.dist * DRG.zoom };
  DRG.yaw = 0; DRG.pitch = 0; DRG.zoom = 1;
  var g = camGoal(), d = v3len(v3sub(a.t, g.t));
  FLY = { a: a, t: 0, dur: RM ? 0.9 : clamp(1.4 + d * 0.04, 1.5, 2.6), hop: hop || 0, hp: hop ? 0.18 : 0 };
}
function camUpdate(dt) {
  if (view === 1) tourStep(dt);
  var g = camGoal();
  if (FLY) {
    FLY.t = Math.min(1, FLY.t + dt / FLY.dur);
    var u = easeIO(FLY.t), a = FLY.a, sn = Math.sin(Math.PI * u);
    CV.t = v3lerp(a.t, g.t, u); CV.yaw = a.yaw + angDiff(a.yaw, g.yaw) * u;
    CV.pitch = lerp(a.pitch, g.pitch, u) + FLY.hp * sn; CV.dist = lerp(a.dist, g.dist, u) * (1 + FLY.hop * sn);
    if (FLY.t >= 1) FLY = null;
  } else {
    var k = 1 - Math.exp(-dt * (V.k === "ps" ? 3.0 : 2.2)), sway = (RM || view === 2) ? 0 : 0.03 * Math.sin(now * 0.09);
    CV.t = v3lerp(CV.t, g.t, k); CV.yaw += angDiff(CV.yaw, g.yaw + sway) * k; CV.pitch += (g.pitch - CV.pitch) * k; CV.dist += (g.dist - CV.dist) * k;
  }
  var yaw = CV.yaw + DRG.yaw, pitch = clamp(CV.pitch + DRG.pitch, 0.06, 1.45), dist = CV.dist * DRG.zoom;
  var eye = [CV.t[0] + dist * Math.sin(yaw) * Math.cos(pitch), CV.t[1] + dist * Math.sin(pitch), CV.t[2] + dist * Math.cos(yaw) * Math.cos(pitch)];
  CAMP = eye;
  VIEWM = m4look(eye, CV.t, Math.abs(Math.cos(pitch)) < 0.02 ? [0, 0, -1] : [0, 1, 0]);
  var cx = (L.rx0 + L.rx1) / 2, cy = (L.ry0 + L.ry1) / 2, ox = cx / L.w * 2 - 1, oy = 1 - cy / L.h * 2;
  VP = m4mul(m4persp(L.narrow ? 0.98 : 0.62, L.w / L.h, 0.3, 900, ox, oy), VIEWM);
  CAMR = [VIEWM[0], VIEWM[4], VIEWM[8]]; CAMU = [VIEWM[1], VIEWM[5], VIEWM[9]];
  E.focus = 1 - cy / L.h;
}
function proj(p) {
  var x = VP[0] * p[0] + VP[4] * p[1] + VP[8] * p[2] + VP[12], y = VP[1] * p[0] + VP[5] * p[1] + VP[9] * p[2] + VP[13], w = VP[3] * p[0] + VP[7] * p[1] + VP[11] * p[2] + VP[15];
  if (w < 0.1) return null;
  return [(x / w * 0.5 + 0.5) * L.w, (1 - (y / w * 0.5 + 0.5)) * L.h, w];
}
/* 巡游：全景 → 每个部门（整体 + 一位正在干活的成员特写）→ 随机一个项目 → 回到全景 */
var TOUR = { t: 0, i: -1 };
function tourNext() {
  var nTot = 2 + ND * 2;
  TOUR.i = (TOUR.i + 1) % nTot;
  var i = TOUR.i;
  if (i === 0) { goView("ov", 0, true); TOUR.t = 9; }
  else if (i <= ND * 2) {
    var k = Math.floor((i - 1) / 2);
    if ((i - 1) % 2 === 0) { goView("dp", k, true); TOUR.t = 6.5; }
    else { var c = members(k).filter(function (q) { return q.cur && q.at === q.home; }); var pick0 = c.length ? rpick(c) : (headOf(k) || members(k)[0]); if (pick0) { goView("ps", pick0, true); TOUR.t = 5; } else TOUR.t = 0; }
  } else { var act = PROJS.filter(function (P) { return P.state === "run"; }); if (act.length) { goView("pj", rpick(act), true); TOUR.t = 7.5; } else TOUR.t = 0; }
}
function tourStep(dt) { TOUR.t -= dt; if (TOUR.t <= 0) tourNext(); }
function goView(k, i, fromTour) {
  var pk = V.k, same = pk === k && (k === "ov" || (k === "dp" && V.d === i) || (k === "pj" && V.P === i) || (k === "ps" && V.p === i));
  if (!fromTour && view === 1) view = 0;
  if (same) { syncButtons(); return; }
  if (k === "ps" && pk !== "ps") V.back = { k: pk, d: V.d, P: V.P };
  if (k === "dp") V.d = i; else if (k === "pj") V.P = i; else if (k === "ps") V.p = i;
  V.k = k;
  startFly(pk === "ov" || k === "ov" ? 0 : 0.3);
  setCls(board, "board " + k);
  buildBottom();
  logDirty = true;
  syncButtons(); updatePanels(true);
}
function goBack() { var b = V.back; V.back = null; if (b && b.k === "dp") goView("dp", b.d); else if (b && b.k === "pj" && b.P && PROJS.indexOf(b.P) >= 0) goView("pj", b.P); else goView("ov", 0); }

/* =====================================================================
   21. 画人：坐 / 站 / 走 / 懒人沙发；打字、审稿、端着文件、开会、喝咖啡各有动作；Chief 头顶金色王冠光环
   ===================================================================== */
function docAt(pos, yaw, it, s) {
  var c = Math.cos(yaw) * s, sn = Math.sin(yaw) * s;
  batAddM(DOCT, [c, 0, -sn, 0, 0, s, 0, 0, sn, 0, c, 0, pos[0], pos[1], pos[2], 1], it.P.colL, it.rw ? REDL : OKL, null);
}
function armM(TM, sd, ax, az) { return m4mul(TM, m4RT(m3mul(rotZ(az * sd), rotX(ax)), [sd * 0.2, 0.285, 0])); }
function drawPerson(p) {
  var sk = p.sk, home = p.at === p.home, walk = p.mode === "walk" && sk < 0.02, pl = p.at || (sk > 0.02 ? p.from : p.dest) || p.home;
  var bean = pl && pl.pose === "bean", seatY = pl && pl.pose !== "stand" ? pl.sh : SEAT_H;
  var hipY = lerp(0.445, seatY + 0.03, sk), sw = walk ? Math.sin(p.ph) : 0, bob = walk ? Math.abs(Math.cos(p.ph)) * 0.028 : 0;
  var M = m4RT(rotY(p.yaw), [p.x, groundY(p.x, p.z), p.z]), hz = 0.05 * sk, lag = Math.sin(p.ph - 1.3), pi = p.id * 1.7;
  if (p.isChief) M = m4mul(M, m4S(1.15));
  var thS = bean ? -1.15 : -Math.PI / 2, shS = bean ? -0.55 : 0;
  for (var sd = -1; sd <= 1; sd += 2) {
    var thW = walk ? 0.5 * sw * sd : 0, shW = walk ? thW + 0.55 * Math.max(0, lag * sd) : 0;
    var thA = lerp(thW, thS, sk), shA = lerp(shW, shS, sk), hx = sd * 0.075;
    batAddM(PH.thigh, m4mul(M, m4RT(rotX(thA), [hx, hipY + bob, hz])), null, null, null);
    batAddM(PH.shin, m4mul(M, m4RT(rotX(shA), [hx, hipY + bob - 0.2 * Math.cos(thA), hz - 0.2 * Math.sin(thA)])), null, null, null);
  }
  var it = p.cur, working = home && it && p.mode !== "walk", typing = working && it.st === "work", reviewing = working && it.st === "rev";
  var meet = p.task === "meet" && !walk, lounge = p.task === "lounge" && !walk, talk = p.talkT > 0;
  var lean = typing ? 0.1 : (reviewing ? 0.05 : (bean ? -0.28 : (lounge && sk > 0.5 ? -0.12 : 0)));
  p.lean += (lean - p.lean) * 0.15;
  var TM = m4mul(M, m4RT(rotX(p.lean), [0, hipY + bob, hz]));
  batAddM(p.ai ? PH.torsoA : PH.torsoH, TM, p.shL, null, p.dcL);
  var xL = 0, zL = 0.07, xR = 0, zR = 0.07, ti = now * 15 + pi;
  if (p.carry) { xL = xR = -1.05; zL = zR = -0.22; }
  else if (walk) { xL = -0.45 * sw; xR = 0.45 * sw; zL = zR = 0.06; }
  else if (p.isChief && home) { xL = -0.85; xR = talk ? -1.5 + 0.35 * Math.sin(now * 6) : -0.85 + 0.08 * Math.sin(now * 3); zL = zR = -0.18; }
  else if (typing) { xL = -1.2 + 0.07 * Math.sin(ti); xR = -1.2 + 0.07 * Math.sin(ti + 1.9); zL = zR = -0.2; }
  else if (reviewing) { if (p.ai) { xL = xR = -1.15; zL = zR = -0.15; } else { xL = -2.0; zL = -0.55; xR = -1.0; zR = -0.1; } }
  else if (meet) { xL = -0.9; zL = -0.12; xR = talk ? -1.5 + 0.35 * Math.sin(now * 6) : -0.9; zR = -0.12; }
  else if (lounge && sk < 0.5) { xR = -1.7 + 0.25 * Math.sin(now * 0.8 + pi); zR = -0.4; }
  else if (sk > 0.5) { xL = xR = lounge ? -0.35 : -0.62; zL = zR = -0.05; }
  else if (talk) { xR = -1.3 + 0.3 * Math.sin(now * 6); zR = 0.1; }
  batAddM(PH.arm, armM(TM, 1, xL, zL), p.shL, null, null);
  batAddM(PH.arm, armM(TM, -1, xR, zR), p.shL, null, null);
  var hx2 = typing || reviewing ? 0.14 + 0.03 * Math.sin(now * 2.2 + pi) : (lounge ? -0.08 : 0), hy = (!walk && !typing && !reviewing && !meet && !p.carry) ? 0.4 * Math.sin(now * 0.33 + pi * 1.3) : 0;
  if (meet) hy = 0.3 * Math.sin(now * 0.5 + pi);
  var HM = m4mul(TM, m4RT(m3mul(rotY(hy), rotX(hx2)), [0, 0.53, 0]));
  batAddM(PH.head[p.hd], HM, null, null, null);
  batAddM(PH.hair[p.hs], HM, null, p.hcL, null);
  if (p.ai) batAddM(HALO, m4mul(M, m4T(0, hipY + bob + 0.92 + 0.025 * Math.sin(now * 2 + pi), hz)), null, null, AIC);
  if (p.isChief) batAddM(CROWN, m4mul(M, m4mul(m4T(0, hipY + bob + 1.0 + 0.03 * Math.sin(now * 1.6), hz), m4RT(rotY(now * 0.8), [0, 0, 0]))), null, null, null);
  if (p.carry) docAt(m4pt(TM, [0, 0.16, 0.25]), p.yaw + 0.1, p.carry, 1);
}
function headPt(p, up) { var pl = p.at || (p.sk > 0.02 ? p.from : null), s = p.isChief ? 1.15 : 1; var hip = lerp(0.445, ((pl && pl.pose !== "stand") ? pl.sh : SEAT_H) + 0.03, p.sk); return [p.x, groundY(p.x, p.z) + (hip + 0.53) * s + (up || 0), p.z]; }

/* =====================================================================
   22. 场景组装 + 特效（全息进度柱、飞行文件夹、审核光环、会议室灯光、项目流转弧线）
   ===================================================================== */
var DSTAT = DEPTS.map(function () { return { prog: 0, busy: 0, q: 0, rv: 0, dn: 0, busyF: 0.5, andon: 0, n: 0 }; });
var GOLD = [1.0, 0.8, 0.38], CYAN = [0.42, 0.88, 1.0];
function bez(a, b, h, u) { var c = [(a[0] + b[0]) / 2, Math.max(a[1], b[1]) + h, (a[2] + b[2]) / 2], v = 1 - u; return [v * v * a[0] + 2 * v * u * c[0] + u * u * b[0], v * v * a[1] + 2 * v * u * c[1] + u * u * b[1], v * v * a[2] + 2 * v * u * c[2] + u * u * b[2]]; }
function arc(a, b, h, c, al, dots, spd) {
  var n = 18, i, prev = a;
  for (i = 1; i <= n; i++) { var q = bez(a, b, h, i / n); fxStreak(prev, q, 0.09, c, al * (0.55 + 0.45 * Math.sin(i / n * Math.PI))); fxStreak(prev, q, 0.025, [1, 1, 1], al * 0.35); prev = q; }
  for (i = 0; i < dots; i++) { var u = (now * spd + i / dots) % 1, dp = bez(a, b, h, u); fxDot(dp, 0.55, c, 0.95 * Math.sin(u * Math.PI)); fxDot(dp, 0.18, [1, 1, 1], 0.8 * Math.sin(u * Math.PI)); }
}
function buildScene(t, dt) {
  DRAWS = []; FXA.n = 0; FXB.n = 0; BAT.nv = 0; BAT.ni = 0; BAT.dirty = true;
  var N = E.night, LMP = E.lamp, i, k;
  dq(OFFICE);
  var mOn = MT && MT.ph === "on";
  dq(ROOMM.meet, IDM, GOLD, mOn && MT.loc === "meet" ? 1 : 0.05);
  for (k = 0; k < ND; k++) dq(DEPTM[k], IDM, DEPTS[k].col, 0.25 + 0.75 * DSTAT[k].busyF, DSTAT[k].andon);
  PEOPLE.forEach(drawPerson);
  PEOPLE.forEach(function (p) {
    if (p.gone) return;
    var n = Math.min(p.q.length, 6);
    for (i = 0; i < n; i++) docAt([p.tray[0], p.tray[1] + 0.004 + i * 0.034, p.tray[2]], p.trayYaw + (i % 2 ? 0.07 : -0.05), p.q[i], 1);
    if (p.cur && p.at === p.home && !p.isChief) docAt(p.deskPt, p.trayYaw + 0.25, p.cur, 0.95);
  });
  FLYS.forEach(function (it) {
    var u = it.ft, h = it.fh * 4 * u * (1 - u), p0 = v3lerp(it.fa, it.fb, u); p0[1] += h;
    var u2 = Math.max(0, u - 0.07), p1 = v3lerp(it.fa, it.fb, u2); p1[1] += it.fh * 4 * u2 * (1 - u2);
    var c = it.P.col;
    docAt(p0, now * 3 + it.k, it, 1.2);
    fxStreak(p1, p0, 0.035, c, 0.75); fxDot(p0, 0.22, c, 0.55 + 0.3 * N);
  });
  /* 总经理办公室：北墙大屏上的项目进度柱状图（每根柱 = 一个在推进的项目）+ 台灯 / 屏幕的光 */
  var act = PROJS.filter(function (P) { return P.state === "run" || now - P.doneAt < 6; }).slice(0, 10), nA = act.length, sz = GM_SCR[1] + 0.012, sy0 = 0.76;
  fxStreak([-1.38, sy0 - 0.03, sz], [1.38, sy0 - 0.03, sz], 0.008, [0.75, 0.9, 1.0], 0.55);
  act.forEach(function (P, j) {
    var x = nA > 1 ? -1.25 + 2.5 * j / (nA - 1) : 0, h = 0.03 + 0.68 * (P.pp || 0);
    fxStreak([x, sy0, sz], [x, sy0 + h, sz], 0.07, P.col, 0.9, 1); fxDot([x, sy0 + h + 0.02, sz + 0.005], 0.07, P.col, 0.85);
  });
  fxDot([GM_SCR[0], 1.13, sz + 0.02], 1.3, CYAN, 0.05 + 0.08 * N);
  fxDot([0.95, 0.85, HOLO[1] - 0.24], 0.35, [1.0, 0.85, 0.55], 0.25 * N);
  /* 审核中的人脚下一圈光环（真人金色 / Agent 青色） */
  PEOPLE.forEach(function (p) {
    if (p.cur && p.cur.st === "rev" && p.at === p.home) fxRingH([p.x, 0.045, p.z], 0.62 + 0.05 * Math.sin(now * 4 + p.id), p.ai ? CYAN : GOLD, 0.75);
    if (p.ai && N > 0.3 && p.cur && p.at === p.home) fxDot(headPt(p, 0.39), 0.16, CYAN, 0.25 * N);
  });
  for (i = 0; i < RINGS.length; i++) { var R0 = RINGS[i], er = (now - R0.t0) / 1.3; if (er < 0 || er >= 1) continue; fxRingH(R0.p, R0.r * (0.3 + 1.4 * (1 - Math.pow(1 - er, 3))), R0.c, 0.9 * (1 - er)); }
  if (mOn) { fxPool([MT.c[0], 0.04, MT.c[1]], 2.6, GOLD, 0.22 + 0.06 * Math.sin(now * 2)); fxDot([MT.c[0], 1.3, MT.c[1]], 1.0, GOLD, 0.18 + 0.2 * N); }
  /* 选中高亮 */
  if (V.k === "dp") {
    var D = DEPTS[V.d], R = D.R, dc = D.col, y = 1.12, al = 0.55 + 0.15 * Math.sin(now * 2.5);
    var c0 = [R.x0, y, R.z0], c1 = [R.x1, y, R.z0], c2 = [R.x1, y, R.z1], c3 = [R.x0, y, R.z1];
    fxStreak(c0, c1, 0.035, dc, al, 1); fxStreak(c1, c2, 0.035, dc, al, 1); fxStreak(c2, c3, 0.035, dc, al, 1); fxStreak(c3, c0, 0.035, dc, al, 1);
    fxPool([D.cx, 0.04, D.cz], Math.max(R.hw, R.hd) * 1.2, dc, 0.1);
  } else if (V.k === "pj" && V.P) {
    var P = V.P, hp = [HOLO[0], 1.4, HOLO[1]];
    arc(hp, v3add(stagePt(P.S[0]), [0, 1.3, 0]), 3.5, CYAN, 0.4, 2, 0.4);
    for (i = 0; i < P.ns - 1; i++) { var A0 = v3add(stagePt(P.S[i]), [0, 1.3, 0]), B0 = v3add(stagePt(P.S[i + 1]), [0, 1.3, 0]); if (v3len(v3sub(A0, B0)) > 0.5) arc(A0, B0, 2.6 + v3len(v3sub(A0, B0)) * 0.12, P.col, 0.85, 3, 0.32); }
    arc(v3add(stagePt(P.S[P.ns - 1]), [0, 1.3, 0]), hp, 3.5, GOLD, 0.4, 2, 0.4);
    P.S.forEach(function (S) { var sp = stagePt(S); fxPool([sp[0], 0.04, sp[2]], 3.2, S.d >= 0 ? DEPTS[S.d].col : GOLD, 0.12); });
    PEOPLE.forEach(function (p) { if (holds(p, P)) fxRingH([p.x, groundY(p.x, p.z) + 0.05, p.z], 0.5, P.col, 0.7); });
  } else if (V.k === "ps" && V.p) {
    var sp2 = V.p;
    var gy2 = groundY(sp2.x, sp2.z);
    fxRingH([sp2.x, gy2 + 0.05, sp2.z], 0.62 + 0.04 * Math.sin(now * 3), sp2.ai ? CYAN : GOLD, 0.9); fxPool([sp2.x, gy2 + 0.04, sp2.z], 1.4, sp2.ai ? CYAN : GOLD, 0.16);
  }
  /* 夜里：房间顶灯光池 + 路灯 */
  if (LMP > 0.02) {
    DEPTS.forEach(function (D0) { fxPool([D0.cx, 0.03, D0.cz], Math.max(D0.R.hw, D0.R.hd) * 1.05, [1.0, 0.82, 0.6], 0.16 * LMP); });
    fxPool([0, 0.03, -0.6], 5.2, [1.0, 0.8, 0.55], 0.24 * LMP); fxPool([HOLO[0], 0.65, HOLO[1]], 1.6, [1.0, 0.85, 0.6], 0.2 * LMP);
    LAMPS.forEach(function (Lp) { fxPool([Lp[0], GY + 0.03, Lp[2]], 2.8, [1.0, 0.78, 0.5], 0.4 * LMP); fxDot([Lp[0], Lp[1] - 0.02, Lp[2]], 0.5, [1.0, 0.82, 0.55], 0.6 * LMP); });
  }
  for (i = 0; i < 24; i++) { LPA[i] = 0; LCA[i] = 0; }
  function light(n, p, c, s) { LPA[n * 3] = p[0]; LPA[n * 3 + 1] = p[1]; LPA[n * 3 + 2] = p[2]; LCA[n * 3] = c[0] * s; LCA[n * 3 + 1] = c[1] * s; LCA[n * 3 + 2] = c[2] * s; }
  light(0, [HOLO[0], 2.0, HOLO[1] + 0.6], [1.0, 0.84, 0.6], 0.3 + 2.1 * N);
  /* 8 个房间只有 6 盏灯可用：选离镜头目标近的 */
  var order = DEPTS.map(function (D0, k0) { return [hyp(D0.cx - CV.t[0], D0.cz - CV.t[2]), k0]; }).sort(function (a, b) { return a[0] - b[0]; });
  for (i = 0; i < 6; i++) { var D1 = DEPTS[order[i][1]]; light(1 + i, [D1.cx, 2.5, D1.cz], [1.0, 0.82, 0.62], 0.06 + 0.95 * N); }
  var mc = MT ? MT.c : MEET_C; light(7, [mc[0], 1.8, mc[1]], [1.0, 0.82, 0.55], mOn ? 0.4 + 1.4 * N : 0.7 * N);
}

/* =====================================================================
   23. HUD：部门牌 / Chief 牌 / 房间牌 / 人员名牌 / 对话气泡
   ===================================================================== */
function say(p, text, cls, pri) {
  if (!p) return;
  pri = pri || 0;
  var b = null, i, oldest = null;
  for (i = 0; i < BUBS.length; i++) if (BUBS[i].p === p && now - BUBS[i].t < BUBS[i].life) { b = BUBS[i]; break; }
  if (!b) for (i = 0; i < BUBS.length; i++) { var B0 = BUBS[i]; if (now - B0.t >= B0.life) { b = B0; break; } if (!oldest || B0.t < oldest.t) oldest = B0; }
  if (!b) { if ((oldest.pri || 0) > pri && now - oldest.t < 1.4) return; b = oldest; }
  b.p = p; b.t = now; b.life = 2.3 + Math.min(2, text.length * 0.07); b.pri = pri; b.w = 0;
  b.el.className = "bb" + (cls ? " " + cls : ""); b.el.textContent = text;
  p.talkT = Math.max(p.talkT, 1.3);
}
function placeAt(Lb, x, y) {
  var tf0 = "translate3d(" + x.toFixed(1) + "px," + y.toFixed(1) + "px,0)";
  if (Lb.tf !== tf0) { Lb.tf = tf0; Lb.el.style.transform = tf0; }
  if (!Lb.vis) { Lb.vis = true; Lb.el.style.opacity = "1"; }
}
function hideLabel(Lb) { if (Lb && Lb.vis) { Lb.vis = false; Lb.el.style.opacity = "0"; } }
function inRegion(q, m) { m = m || 0; return q && q[0] > L.rx0 - m && q[0] < L.rx1 + m && q[1] > L.ry0 - m && q[1] < L.ry1 + 40; }
function bx(q) { return (q[0] - L.frX) / L.bs; }
function by(q) { return (q[1] - L.frY) / L.bs; }
function leader(an, dy, c) {
  fx.strokeStyle = hexA(c, 0.85); fx.lineWidth = 1.2; fx.setLineDash([3, 3]);
  fx.beginPath(); fx.moveTo(an[0], an[1] - dy * L.bs); fx.lineTo(an[0], an[1]); fx.stroke(); fx.setLineDash([]);
  fx.fillStyle = hexA(c, 0.95); fx.beginPath(); fx.arc(an[0], an[1], 3, 0, TAU); fx.fill();
}
var TAGVIS = {}, TOPY = 68;
function placeHud() {
  var allTags = PEOPLE.map(function (p) { return p.tag; });
  if (L.narrow || !VP) { LABELS.concat(RLAB, [CHL], allTags, BUBS).forEach(hideLabel); return; }
  fx.setTransform(L.dpr2, 0, 0, L.dpr2, 0, 0); fx.clearRect(0, 0, L.w, L.h);
  var settled = !FLY || FLY.t > 0.85, k;
  var lx0 = (L.rx0 - L.frX) / L.bs, lx1 = (L.rx1 - L.frX) / L.bs;
  for (k = 0; k < ND; k++) {
    var Lb = LABELS[k], D = DEPTS[k], show = settled && (V.k === "ov" || (V.k === "pj" && V.P && V.P.S.some(function (s) { return s.d === k; })));
    var aw = rw(D.R, D.R.head[0], D.R.head[1] + 0.3), an = show ? proj([aw[0], 1.05, aw[1]]) : null;
    if (!an || !inRegion(an, 30)) { hideLabel(Lb); continue; }
    var dy = 30;
    placeAt(Lb, clamp(bx(an) - Lb.w / 2, lx0, lx1 - Lb.w), Math.max(TOPY, by(an) - Lb.h - dy));
    leader(an, dy, D.c);
  }
  var ca = settled && (V.k === "ov" || V.k === "pj") && CHIEF ? proj(headPt(CHIEF, 0.75)) : null;
  if (ca && inRegion(ca, 20)) { placeAt(CHL, clamp(bx(ca) - CHL.w / 2, lx0, lx1 - CHL.w), Math.max(TOPY, by(ca) - CHL.h - 22)); leader(ca, 22, CHIEF_C); } else hideLabel(CHL);
  var RA = [[MEET_C[0], 1.25, MEET_C[1]], [4.0, 1.2, 9.2], [-1.2, 1.3, 10.8]];
  RLAB.forEach(function (Lr, r) {
    var a0 = settled && V.k === "ov" ? proj(RA[r]) : null;
    if (!a0 || !inRegion(a0, 20)) { hideLabel(Lr); return; }
    placeAt(Lr, clamp(bx(a0) - Lr.w / 2, lx0, lx1 - Lr.w), by(a0) - Lr.h - 6);
  });
  var want = [];
  if (settled && V.k === "dp") want = members(V.d);
  else if (settled && V.k === "ps" && V.p) want = [V.p];
  var rects = []; TAGVIS = {};
  PEOPLE.forEach(function (p) {
    var T0 = p.tag;
    if (want.indexOf(p) < 0) { hideLabel(T0); return; }
    var q = proj(headPt(p, 0.42));
    if (!q || !inRegion(q, 20)) { hideLabel(T0); return; }
    rects.push({ T: T0, p: p, x: bx(q) - T0.w / 2, y: Math.max(TOPY, by(q) - T0.h), ax: q[0], ay: q[1] });
  });
  rects.sort(function (a, b) { return b.y - a.y; });
  var placed = [];
  rects.forEach(function (r) {
    for (var g = 0; g < 8; g++) { var hit = null; for (var j = 0; j < placed.length; j++) { var o = placed[j]; if (r.x < o.x + o.T.w + 4 && r.x + r.T.w + 4 > o.x && r.y < o.y + o.T.h + 3 && r.y + r.T.h + 3 > o.y) { hit = o; break; } } if (!hit) break; r.y = hit.y - r.T.h - 4; if (r.y < TOPY) { r.y = hit.y + hit.T.h + 4; r.x += 0; } }
    placed.push(r); placeAt(r.T, r.x, r.y); TAGVIS[r.p.id] = r;
    if (by([0, r.ay]) - (r.y + r.T.h) > 10) { fx.strokeStyle = "rgba(255,226,186,.45)"; fx.lineWidth = 1; fx.beginPath(); fx.moveTo(r.ax, (r.y + r.T.h) * L.bs + L.frY); fx.lineTo(r.ax, r.ay); fx.stroke(); }
  });
  BUBS.forEach(function (B) {
    if (!B.p || now - B.t > B.life || !settled || B.p.dead) { hideLabel(B); return; }
    var q = proj(headPt(B.p, 0.5));
    if (!q || !inRegion(q, 10)) { hideLabel(B); return; }
    if (!B.w) { B.w = B.el.offsetWidth || 100; B.h = B.el.offsetHeight || 26; }
    var tv = TAGVIS[B.p.id], x = bx(q) - 18, y = tv ? tv.y - B.h - 10 : by(q) - B.h - 8;
    B.el.style.opacity = String(clamp((B.life - (now - B.t)) / 0.4, 0, 1));
    placeAt(B, x, y); B.vis = true;
  });
}

/* =====================================================================
   24. 面板
   ===================================================================== */
function pStatus(p) {
  if (p.gone) return ["离开中", ""];
  if (p.mode === "walk") return p.task === "deliver" ? ["送文件", "b"] : (p.task === "meet" ? ["去开会", "m"] : (p.task === "lounge" ? ["去休息", ""] : (p.task === "patrol" ? ["巡视中", "b"] : (p.at === null && p.from === GATE ? ["入职报到", "w"] : ["回工位", ""]))));
  if (p.task === "meet") return MT && MT.ph === "on" ? ["开会中", "m"] : ["等开会", "m"];
  if (p.task === "lounge") return ["休息中", ""];
  if (p.task === "deliver") return ["交接中", "b"];
  if (p.task === "patrol") return ["巡视中", "b"];
  if (p.cur) return p.cur.st === "rev" ? ["审核中", "r"] : (p.cur.rw ? ["返工中", "r"] : ["工作中", "w"]);
  if (p.isChief) return ["派单中", "w"];
  if (p.q.length) return ["待处理", "r"];
  return [p.ai ? "空闲" : "督办中", ""];
}
var STC = { w: "var(--ok)", r: "var(--warn)", m: "#d7b6ff", b: "#9fd8ff", "": "var(--fg-3)" };
function taskHTML(it, short) {
  if (!it) return "—";
  var P = it.P;
  return '<u style="color:' + P.c + '">' + P.id + "</u>" + esc(it.t) + (short ? "" : " · " + stageNm(it) + (it.st === "rev" || it.kind === "rev" ? "审核" : ""));
}
function pTask(p) { return p.cur || p.carry || p.q[0] || null; }
function stats() {
  DEPTS.forEach(function (D, k) {
    var S0 = DSTAT[k], busy = 0, q = 0, rv = 0, dn = 0, rw0 = false, mem = members(k);
    mem.forEach(function (p) { if (p.cur && p.at === p.home) busy++; q += p.q.length + p.inb; dn += p.doneN; if (p.cur && p.cur.rw) rw0 = true; });
    mem.forEach(function (p) { p.q.forEach(function (it) { if (it.kind === "rev") rv++; }); if (p.cur && p.cur.st === "rev") rv++; });
    S0.prog = deptProg(k); S0.busy = busy; S0.q = q; S0.rv = rv; S0.dn = dn; S0.n = mem.length; S0.busyF = mem.length ? busy / mem.length : 0; S0.andon = rw0 ? 2 : (q > Math.max(2, mem.length * 2) ? 1 : 0);
  });
  PROJS.forEach(function (P) { P.pp = projProg(P); });
}
/* 03 项目进度：按项目动态建行 */
var PRR = {};
function buildProjRows() {
  projDirty = false;
  var box = $("pl"), keep = {};
  PROJS.forEach(function (P) {
    keep[P.id] = 1;
    var R = PRR[P.id];
    if (!R) {
      var d = document.createElement("div"), st = "";
      d.className = "pr"; d.style.setProperty("--c", P.c); d.setAttribute("data-pid", P.id); d.setAttribute("role", "button"); d.tabIndex = 0;
      P.S.forEach(function (s) { st += '<i style="--dc:' + dColor(s.d) + '" title="' + s.zh + " · " + dName(s.d) + '"><u></u></i>'; });
      d.innerHTML = '<span class="nm"><u>' + P.id + "</u><b>" + esc(P.zh) + '</b><i class="pill s-ok">推进中</i></span><span class="pc">0%</span><span class="stg">' + st + "</span>" +
        '<span class="ft"><span><b class="ow">—</b>牵头</span><span><b class="dn">0</b>/ ' + P.N + ' 项</span><span class="cur">—</span><span class="r">—</span></span>';
      R = PRR[P.id] = { el: d, pill: d.querySelector(".pill"), pc: d.querySelector(".pc"), segs: [].slice.call(d.querySelectorAll(".stg u")), ow: d.querySelector(".ow"), dn: d.querySelector(".dn"), cur: d.querySelector(".cur"), ph: d.querySelector(".r") };
    }
    box.appendChild(R.el);
  });
  for (var id in PRR) if (!keep[id]) { if (PRR[id].el.parentNode) PRR[id].el.parentNode.removeChild(PRR[id].el); delete PRR[id]; }
  setTxt($("plMeta"), PROJS.filter(function (P) { return P.state === "run"; }).length + " ACTIVE · " + PROJS.length + " 个");
  var mp = $("mPj"), h = "";
  PROJS.forEach(function (P) { h += '<button type="button" data-pid="' + P.id + '" style="--c:' + P.c + '"><b>' + P.id + " " + esc(P.zh) + "</b><em>0%</em></button>"; });
  setHTML(mp, h);
  if (V.k === "pj") buildBottom();
}
/* 底栏 · 全景：人员看板（Chief + 8 部门） */
var SC = [];
function buildStaff() {
  if (!$("sbd")) return;
  var box = $("sbd"), groups = [{ d: -1, zh: "Chief", c: CHIEF_C }].concat(DEPTS.map(function (D, k) { return { d: k, zh: D.zh.replace("行政部", ""), c: D.c }; }));
  box.innerHTML = ""; SC = [];
  groups.forEach(function (g) {
    var col0 = document.createElement("div"); col0.className = "sg"; col0.style.setProperty("--c", g.c);
    var hh = document.createElement("h4"); hh.setAttribute("data-d", g.d); hh.innerHTML = g.zh + "<em>" + (g.d >= 0 ? "0%" : "") + "</em>"; col0.appendChild(hh);
    if (g.d >= 0) SC.push({ hd: true, d: g.d, em: hh.querySelector("em") });
    var ps = g.d < 0 ? [CHIEF] : members(g.d).sort(function (a, b) { return (b.head ? 1 : 0) - (a.head ? 1 : 0); });
    ps.forEach(function (p) {
      var c = document.createElement("div"); c.className = "sc"; c.setAttribute("data-pid", p.id); c.setAttribute("style", avStyle(p)); c.setAttribute("role", "button"); c.tabIndex = 0;
      c.innerHTML = '<i class="av"></i><span class="n"><b>' + esc(p.n) + "</b>" + (p.ai ? "" : '<i class="tag-hu" style="padding:2px 3px;font-size:7px">真人</i>') + '<em>—</em></span><span class="bar"><i></i></span>';
      col0.appendChild(c);
      SC.push({ p: p, el: c, em: c.querySelector(".n em"), bar: c.querySelector(".bar i") });
    });
    box.appendChild(col0);
  });
}
var MTR = [];
function findP(id) { id = +id; for (var i = 0; i < PEOPLE.length; i++) if (PEOPLE[i].id === id) return PEOPLE[i]; return null; }
function findPJ(id) { for (var i = 0; i < PROJS.length; i++) if (PROJS[i].id === id) return PROJS[i]; return null; }
function buildBottom() {
  var k = V.k, h = "";
  if (k === "dp") {
    var mem = members(V.d).sort(function (a, b) { return (b.head ? 1 : 0) - (a.head ? 1 : 0); });
    MTR = []; $("mtb").innerHTML = "";
    if (!mem.length) $("mtb").innerHTML = '<div class="hr-empty">这个部门还没有成员，点右上角「人员管理」新增</div>';
    mem.forEach(function (p) {
      var r = document.createElement("div");
      r.className = "mtr"; r.setAttribute("data-pid", p.id); r.setAttribute("style", avStyle(p)); r.setAttribute("role", "button"); r.tabIndex = 0;
      r.innerHTML = '<span class="who"><i class="av"></i><b>' + esc(p.n) + "</b>" + badge(p) + "<em>" + esc(p.role) + (p.head ? " · 负责人" : "") + '</em></span><span class="st">—</span><span class="tk">—</span><span class="bar"><i></i></span><span class="v d">0</span><span class="v t">—</span>';
      $("mtb").appendChild(r);
      MTR.push({ p: p, st: r.querySelector(".st"), tk: r.querySelector(".tk"), bar: r.querySelector(".bar i"), d: r.querySelector(".v.d"), t: r.querySelector(".v.t") });
    });
  }
  if (k === "pj") PROJS.slice(0, 7).forEach(function (P) { h += '<button type="button" data-pid="' + P.id + '" style="--c:' + P.c + '"' + (P === V.P ? ' class="on"' : "") + "><i></i>" + P.id + "</button>"; });
  else if (k === "ps" && V.p) { var p0 = V.p; (p0.isChief ? [CHIEF] : members(p0.d)).forEach(function (q) { h += '<button type="button" data-pid="' + q.id + '" style="--c:' + dColor(q.d) + '"' + (q === V.p ? ' class="on"' : "") + "><i></i>" + esc(q.n) + "</button>"; }); }
  setHTML($("sel"), h);
  [].forEach.call(SR.querySelectorAll("#vt button"), function (b) { setCls(b, b.getAttribute("data-v") === k ? "on" : ""); });
  var tt = { ov: ["项目进度", "PROJECTS · " + PROJS.filter(function (P) { return P.state === "run"; }).length + " 个推进中"], dp: ["部门成员", "MEMBERS · " + DEPTS[V.d].en], pj: ["项目流水", "PIPELINE · " + (V.P ? V.P.id : "")], ps: ["个人档案", "PROFILE · " + (V.p ? (V.p.isChief ? "CHIEF" : (V.p.ai ? "AGENT" : "HUMAN")) : "")] }[k];
  setTxt($("btmT"), tt[0]); setTxt($("btmE"), tt[1]);
  mini.classList.toggle("in", k !== "ov");
  mini.style.setProperty("--c", k === "dp" ? DEPTS[V.d].c : (k === "pj" && V.P ? V.P.c : (k === "ps" && V.p ? dColor(V.p.d) : "#ffc46b")));
}
function syncButtons() {
  [].forEach.call(SR.querySelectorAll('[data-f="mode"]'), function (b) { b.textContent = VIEWS[view]; });
  setTxt($("crSub"), view === 1 ? "AUTO TOUR" : (view === 2 ? "TOP VIEW" : "OVERVIEW"));
  var nm = V.k === "ov" ? "全景" + (view ? " · " + VIEWS[view] : "") : (V.k === "dp" ? DEPTS[V.d].zh : (V.k === "pj" && V.P ? V.P.id + " " + V.P.zh : (V.p ? V.p.n : "")));
  setTxt($("fScene"), nm);
  if (V.k === "dp") { var D = DEPTS[V.d]; setTxt($("crDn"), pad(V.d + 1, 2)); setTxt($("crDName"), D.zh); setTxt($("crDEn"), D.en); setVar($("crDp"), "--c", D.c); }
  if (V.k === "pj" && V.P) { setTxt($("crPn"), V.P.id); setTxt($("crPName"), V.P.zh); setVar($("crPj2"), "--c", V.P.c); }
  if (V.k === "ps" && V.p) { var p = V.p; setHTML($("crSt"), badge(p)); setTxt($("crSName"), p.n); setTxt($("crSRole"), p.role + " · " + dName(p.d)); setVar($("crPs"), "--c", dColor(p.d)); }
  DR.forEach(function (R, k) { setCls(R.el, "dr" + (V.k === "dp" && V.d === k ? " sel" : "")); });
  LABELS.forEach(function (Lb, k) { setCls(Lb.el, "lab" + (V.k === "dp" && V.d === k ? " sel" : "")); });
  setTxt($("mSub"), V.k === "ov" ? "智能总部 · 示例数据" : nm);
}
function renderLog() {
  if (!logDirty) return; logDirty = false;
  var rows = LOG.filter(function (r) { var g = r.g || {}; if (V.k === "dp") return g.d && g.d.indexOf(V.d) >= 0; if (V.k === "pj") return V.P && g.j === V.P.id; if (V.k === "ps") return V.p && g.p && g.p.indexOf(V.p.id) >= 0; return true; });
  logBox.innerHTML = rows.slice(0, 9).map(function (r, i) { return '<div class="lr' + (r.fresh ? " fresh" : "") + (r.k ? " " + r.k : "") + '" style="--c:' + r.c + ";--o:" + (1 - i * 0.085).toFixed(2) + '"><span>' + r.t + '</span><span class="a">' + esc(r.a) + '</span><span class="x">' + esc(r.x) + "</span></div>"; }).join("") || '<div class="lr"><span></span><span></span><span class="x">暂无相关动态</span></div>';
  LOG.forEach(function (r) { r.fresh = false; });
  setTxt($("logMeta"), V.k === "ov" ? "ALL" : (V.k === "dp" ? DEPTS[V.d].en : (V.k === "pj" && V.P ? V.P.id : (V.p ? V.p.n : ""))));
}
function updatePanels(force) {
  if (rosterDirty) { rosterDirty = false; buildStaff(); if (V.k === "dp" || V.k === "ps") buildBottom(); if (HRUI.open) hrRender(); }
  if (projDirty) buildProjRows();
  if (V.k === "ps" && V.p && V.p.dead) goBack();
  if (V.k === "pj" && V.P && PROJS.indexOf(V.P) < 0) goView("ov", 0);
  stats();
  var cp = companyProg(), i, k, liv = living(), nAI = 0, nHu = 0;
  liv.forEach(function (p) { if (p.ai) nAI++; else nHu++; });
  setTxt($("subN"), String(liv.length)); setTxt($("subA"), nAI + " 个 Agent"); setTxt($("subH"), nHu + " 位真人督办");
  setTxt($("sStaff"), String(liv.length)); setTxt($("sAI"), String(nAI)); setTxt($("sHu"), String(nHu));
  setHTML($("cP"), Math.round(cp * 100) + "<small>%</small>");
  var wip = 0, back = 0, revN = 0;
  PROJS.forEach(function (P) { P.items.forEach(function (it) { if (it.st === "back") back++; else if (it.st !== "fin") wip++; }); });
  var hs = [];
  liv.forEach(function (p) { if (p.ai) return; var n = 0; p.q.forEach(function (it) { if (it.kind === "rev") n++; }); if (p.cur && p.cur.st === "rev") n++; FLYS.forEach(function (it) { if (it.who === p && it.kind === "rev") n++; }); revN += n; if (hs.length < 4) hs.push(p.n.charAt(0) + n); });
  var cps = $("cPs"), pk = PROJS.map(function (P) { return P.id; }).join(","); if (cps.__n !== pk) { cps.__n = pk; setTxt($("cPn"), PROJS.length + " 个项目加权"); cps.innerHTML = PROJS.map(function (P) { return '<i style="--c:' + P.c + '" title="' + P.id + " " + esc(P.zh) + '"><u></u></i>'; }).join(""); }
  [].forEach.call(cps.querySelectorAll("u"), function (u, j) { if (PROJS[j]) setW(u, PROJS[j].pp); });
  setTxt($("kDone"), String(ST.done)); setTxt($("kDoneD"), "完成阶段 " + ST.stages + " 个");
  setTxt($("kWip"), String(wip)); setTxt($("kWipD"), "Chief 待派 " + back);
  setTxt($("kRev"), String(revN)); setTxt($("kRevD"), hs.join(" · ") || "—");
  setCls($("kRevT"), "kt" + (revN >= 5 ? " w" : ""));
  var lead = ST.lead.length ? ST.lead.reduce(function (a, b) { return a + b; }, 0) / ST.lead.length : 0;
  setHTML($("kLead"), Math.round(lead) + "<small>min</small>");
  setHTML($("kRw"), (ST.rev ? (ST.rej / ST.rev * 100).toFixed(1) : "0.0") + "<small>%</small>"); setTxt($("kRwD"), "退回 " + ST.rej + " / 审核 " + ST.rev);
  setTxt($("kTok"), fmtK(ST.tok)); setTxt($("kTokD"), "Agent 用量 · " + fmtK(ST.tokRate) + "/s");
  var busyN = 0; liv.forEach(function (p) { if ((p.cur && p.at === p.home) || p.task === "deliver" || p.task === "meet" || p.task === "patrol" || p.isChief) busyN++; });
  setTxt($("crOn"), String(busyN)); setTxt($("crPj"), String(PROJS.filter(function (P) { return P.state === "run"; }).length));
  setTxt($("fDone"), String(ST.done)); setTxt($("fWip"), String(wip)); setTxt($("fProg"), String(Math.round(cp * 100)));
  setTxt($("coMeta"), "SPRINT 14 · 周" + "日一二三四五六".charAt(new Date().getDay()));
  DEPTS.forEach(function (D, k2) {
    var S0 = DSTAT[k2], R = DR[k2], hd = headOf(k2);
    setHTML(R.hd, hd ? badge(hd) + esc(hd.n) + " · " + S0.n + " 人" : '<i class="tag-hu" style="background:var(--bad);color:#fff">空缺</i>' + S0.n + " 人");
    setTxt(R.pc, Math.round(S0.prog * 100) + "%"); setW(R.bar, S0.prog);
    setTxt(R.a, String(S0.busy)); setTxt(R.q, String(S0.q)); setTxt(R.r, String(S0.rv)); setTxt(R.o, String(S0.dn));
    var Lb = LABELS[k2];
    setTxt(Lb.pc, Math.round(S0.prog * 100) + "%"); setW(Lb.bar, S0.prog);
    if (V.k === "pj" && V.P) { var s3 = [], cnt = 0; V.P.S.forEach(function (S, si) { if (S.d === k2) { s3.push("S" + (si + 1) + " " + S.zh); V.P.items.forEach(function (it) { if (it.s === si && it.st !== "fin" && it.st !== "back") cnt++; }); } }); setHTML(Lb.l3, esc(s3.join(" / ")) + " · 在流转 <b>" + cnt + "</b> 项"); }
    else setHTML(Lb.l3, (hd ? esc(hd.n) + (hd.ai ? "" : "（真人）") + " · " : "负责人空缺 · ") + "<b>" + S0.busy + "</b>在忙 · 排队 <b>" + S0.q + "</b> · 待审 <b>" + S0.rv + "</b>");
  });
  if (!L.narrow) LABELS.concat(RLAB, [CHL]).forEach(function (Lb) { var w = Lb.el.offsetWidth; if (w) { Lb.w = w; Lb.h = Lb.el.offsetHeight; } });
  /* Chief 牌 */
  setTxt(CHL.pc, String(back)); setHTML(CHL.l3, "待派 <b>" + back + "</b> · 在途 <b>" + FLYS.length + "</b> · " + (MT && MT.loc === "gm" ? "周会" + (MT.ph === "on" ? "中" : "集合中") + " · " + MT.ppl.length + " 人" : (CHIEF.q.length ? "待审 <b>" + CHIEF.q.length + "</b>" : pStatus(CHIEF)[0])));
  /* 项目行 */
  PROJS.forEach(function (P) {
    var R = PRR[P.id]; if (!R) return;
    var segs = P.S.map(function () { return 0; }), cnt = P.S.map(function () { return 0; }), nRev = 0;
    P.items.forEach(function (it) { for (var s = 0; s < P.ns; s++) { if (it.st === "fin" || it.s > s) segs[s] += 1; else if (it.s === s && it.st !== "back") { segs[s] += it.st === "work" ? it.prog * 0.9 : (it.st === "rev" ? 0.9 + 0.1 * it.prog : (it.kind === "rev" ? 0.9 : 0)); cnt[s]++; if (it.kind === "rev") nRev++; } } });
    R.segs.forEach(function (u, s) { setW(u, segs[s] / P.N); });
    setTxt(R.pc, Math.round(P.pp * 100) + "%"); setTxt(R.dn, String(P.done)); setTxt(R.ow, projOwner(P).n);
    var st = P.state === "done" ? ["已交付", "s-done"] : (P.urgent ? ["加急", "s-bad"] : (now - P.newAt < 8 ? [P.user ? "新任务" : "新一期", "s-ok"] : (nRev >= 2 ? ["待审 " + nRev, "s-warn"] : ["推进中", "s-ok"])));
    setTxt(R.pill, st[0]); setCls(R.pill, "pill " + st[1]);
    setCls(R.el, "pr" + (P.state === "done" ? " done" : "") + (V.k === "pj" && V.P === P ? " sel" : ""));
    var mx = -1, ms = 0; cnt.forEach(function (c, s) { if (c > mx) { mx = c; ms = s; } });
    setTxt(R.cur, P.state === "done" ? (P.preset ? "下一期 " + Math.max(0, Math.ceil(24 - (now - P.doneAt))) + "s 后启动" : "已归档") : (mx > 0 ? "在做 · " + P.S[ms].zh + " " + mx + " 项" : "排期中"));
    setTxt(R.ph, P.user ? "你派的" : "第 " + P.phase + " 期");
  });
  [].forEach.call($("mPj").querySelectorAll("button"), function (b) { var P = findPJ(b.getAttribute("data-pid")); if (P) setTxt(b.querySelector("em"), P.state === "done" ? "已交付" : Math.round(P.pp * 100) + "%"); });
  var mOn = MT && MT.ph === "on";
  var mr = MT && MT.loc === "meet";
  setTxt(RLAB[0].l3, mr ? (mOn ? "会议中 · " : "集合中 · ") + MT.title + " · " + MT.ppl.length + " 人" : "空闲"); setCls(RLAB[0].el, "lab rm" + (mr ? " on" : ""));
  var ln = 0; liv.forEach(function (p) { if (p.task === "lounge") ln++; });
  setTxt(RLAB[1].l3, ln ? ln + " 人在休息" : "暂时没人");
  setTxt(RLAB[2].l3, "今日入职 " + ST.hired + " · 离开 " + ST.left);
  /* 3D 名牌 */
  PEOPLE.forEach(function (p) {
    var T0 = p.tag; if (!T0.vis && !(V.k === "dp" || V.k === "pj" || V.k === "ps")) return;
    var s = pStatus(p), it = pTask(p);
    setTxt(T0.st, s[0]); T0.st.style.color = STC[s[1]];
    setHTML(T0.t2, it ? taskHTML(it) : (p.isChief ? "拆解 · 派单 · 终审" : (p.ai ? "暂无任务" : "督办本部门")));
    setW(T0.bar, p.cur ? p.cur.prog : 0);
    var w = T0.el.offsetWidth; if (w) { T0.w = w; T0.h = T0.el.offsetHeight; }
  });
  if (V.k === "ov") {
    SC.forEach(function (c) {
      if (c.hd) { setTxt(c.em, Math.round(DSTAT[c.d].prog * 100) + "%"); return; }
      var p = c.p, s = pStatus(p);
      setTxt(c.em, s[0]); setCls(c.em, s[1]); setW(c.bar, p.cur ? p.cur.prog : 0);
    });
  } else if (V.k === "dp") {
    MTR.forEach(function (R) {
      var p = R.p, s = pStatus(p), it = pTask(p), off = it === p.q[0] ? 1 : 0;
      setTxt(R.st, s[0]); R.st.style.color = STC[s[1]];
      setHTML(R.tk, it ? taskHTML(it) + (p.q.length > off ? ' <em style="font-style:normal;color:var(--fg-3)">+' + (p.q.length - off) + " 排队</em>" : "") : "—");
      setW(R.bar, p.cur ? p.cur.prog : 0);
      setTxt(R.d, p.ai ? String(p.doneN) : "审 " + p.revN); setTxt(R.t, p.ai ? fmtK(p.tok) : "—");
    });
  } else if (V.k === "pj") renderPipeline();
  else if (V.k === "ps") renderProfile();
  if (V.k === "dp") setTxt($("crDp2"), Math.round(DSTAT[V.d].prog * 100) + "%");
  if (V.k === "pj" && V.P) setTxt($("crPp"), Math.round(V.P.pp * 100) + "%");
  for (k = 0; k < ND; k++) setTxt(MCH[k], Math.round(DSTAT[k].prog * 100) + "%");
  if (V.k === "ps" && V.p) { var sp = V.p; setTxt($("mS1L"), "状态"); setTxt($("mS1"), pStatus(sp)[0]); setTxt($("mS2L"), sp.ai ? "今日完成" : "今日审核"); setTxt($("mS2"), String(sp.ai ? sp.doneN : sp.revN)); setTxt($("mS3L"), "Token"); setTxt($("mS3"), sp.ai ? fmtK(sp.tok) : "—"); }
  else if (V.k === "dp") { var S1 = DSTAT[V.d]; setTxt($("mS1L"), "部门进度"); setTxt($("mS1"), Math.round(S1.prog * 100) + "%"); setTxt($("mS2L"), "在忙 / 排队"); setTxt($("mS2"), S1.busy + " / " + S1.q); setTxt($("mS3L"), "成员"); setTxt($("mS3"), S1.n + " / " + (DEPTS[V.d].cap + 1)); }
  else if (V.k === "pj" && V.P) { setTxt($("mS1L"), "项目进度"); setTxt($("mS1"), Math.round(V.P.pp * 100) + "%"); setTxt($("mS2L"), "已交付"); setTxt($("mS2"), V.P.done + " / " + V.P.N); setTxt($("mS3L"), V.P.user ? "来源" : "期次"); setTxt($("mS3"), V.P.user ? "你派的" : "第 " + V.P.phase + " 期"); }
  else { setTxt($("mS1L"), "公司总进度"); setTxt($("mS1"), Math.round(cp * 100) + "%"); setTxt($("mS2L"), "进行中"); setTxt($("mS2"), String(wip)); setTxt($("mS3L"), "今日完成"); setTxt($("mS3"), String(ST.done)); }
  renderLog();
}
function renderPipeline() {
  var P = V.P; if (!P) return;
  var h = "";
  P.S.forEach(function (S, s) {
    var list = P.items.filter(function (it) { return it.s === s && it.st !== "fin" && it.st !== "back"; }), done = 0, rv = rvOf(S);
    P.items.forEach(function (it) { if (it.st === "fin" || it.s > s) done++; });
    list.sort(function (a, b) { return (b.st === "work" || b.st === "rev" ? b.prog + 1 : 0) - (a.st === "work" || a.st === "rev" ? a.prog + 1 : 0); });
    h += '<div class="stc" style="--dc:' + dColor(S.d) + '"><h4><u>S' + (s + 1) + "</u>" + esc(S.zh) + "<em>" + list.length + '</em></h4><div class="sd">' + dName(S.d) + (rv && S.d >= 0 ? " · " + esc(rv.n) + "审核" : "") + " · 已过 " + done + "/" + P.N + "</div>";
    list.slice(0, 2).forEach(function (it) {
      var who = it.who ? it.who.n : "—", st = it.st === "work" ? Math.round(it.prog * 100) + "%" : (it.st === "rev" ? "审核 " + Math.round(it.prog * 100) + "%" : (it.st === "fly" ? "传递中" : (it.st === "carry" ? "送过去" : (it.kind === "rev" ? "待审" : "排队"))));
      h += '<div class="ch"><span>' + esc(it.t) + '</span><em class="' + (it.rw ? "r" : "") + '">' + esc(who) + " " + st + '</em><span class="bar" style="--c:' + P.c + '"><i style="width:' + Math.round((it.st === "work" || it.st === "rev" ? it.prog : 0) * 100) + '%"></i></span></div>';
    });
    if (list.length > 2) h += '<div class="more">+' + (list.length - 2) + " 项</div>";
    h += "</div>";
  });
  var last = P.items.filter(function (it) { return it.st === "fin"; }).sort(function (a, b) { return (b.finAt || -1e9) - (a.finAt || -1e9); }).slice(0, 3);
  h += '<div class="stc fin"><h4><u>OK</u>已交付<em>' + P.done + "/" + P.N + '</em></h4><div class="big">' + Math.round(P.pp * 100) + '%</div><div class="sd">' + (P.user ? "你派给 Chief 的任务" : "第 " + P.phase + " 期") + " · 牵头 " + esc(projOwner(P).n) + "</div>";
  last.forEach(function (it) { h += '<div class="ch"><span>' + esc(it.t) + "</span><em>✓</em></div>"; });
  h += "</div>";
  setHTML($("ppl"), h);
}
/* 个人档案：骨架（含编辑 / 移出按钮）只在换人时重建，其余每次只改变化的部分，按钮不会被反复替换 */
var PF = { pid: -1 };
function renderProfile() {
  var p = V.p; if (!p) return;
  var box = $("pfl");
  if (PF.pid !== p.id || PF.chief !== !!p.isChief) {
    PF.pid = p.id; PF.chief = !!p.isChief;
    box.__h = null;
    box.innerHTML = '<div class="pfc pf-c1"><h5>IDENTITY</h5><div class="pf-id"><i class="av"></i><div><b class="pf-nm"></b><em class="pf-ro"></em></div></div>' +
      (p.isChief ? '<div class="pf-sk"><button class="btn sm pri" type="button" data-act="task">＋ 新增任务</button></div>' : '<div class="pf-sk"><button class="btn sm" type="button" data-hr-edit="' + p.id + '">编辑成员</button><button class="btn sm dng" type="button" data-hr-del="' + p.id + '">移出</button></div>') + "</div>" +
      '<div class="pfc"><h5>TODAY</h5><div class="pf-kv"></div><div class="pf-sk pf-sks"></div></div>' +
      '<div class="pfc"><h5>NOW</h5><div class="pf-st"></div><div class="pf-now"></div><span class="bar"><i></i></span><div class="pf-h pf-q"></div></div>' +
      '<div class="pfc"><h5>HISTORY · 今日</h5><div class="pf-h pf-hi"></div></div>';
    PF.c1 = box.querySelector(".pf-c1"); PF.nm = box.querySelector(".pf-nm"); PF.ro = box.querySelector(".pf-ro"); PF.sk = box.querySelector(".pf-sks"); PF.kv = box.querySelector(".pf-kv");
    PF.st = box.querySelector(".pf-st"); PF.now = box.querySelector(".pf-now"); PF.bar = box.querySelector(".bar"); PF.bi = box.querySelector(".bar i"); PF.q = box.querySelector(".pf-q"); PF.hi = box.querySelector(".pf-hi");
    PF.sty = null; PF.stc = null; PF.bc = null;
  }
  var s = pStatus(p), it = pTask(p), sty = avStyle(p);
  if (PF.sty !== sty) { PF.sty = sty; PF.c1.setAttribute("style", sty); }
  setHTML(PF.nm, esc(p.n) + " " + badge(p));
  setTxt(PF.ro, p.role + " · " + dName(p.d) + (p.head ? " 负责人" : ""));
  setHTML(PF.sk, p.skills.map(function (x) { return "<span>" + esc(x) + "</span>"; }).join("") + (p.ai ? "<span>Token " + fmtK(p.tokR) + "/s</span>" : "<span>只审核不干活</span>"));
  setHTML(PF.kv, "<div><span>" + (p.ai ? "今日完成" : "今日审核") + "</span><b>" + (p.ai ? p.doneN : p.revN) + "</b></div><div><span>" + (p.ai ? "审核" : "退回") + "</span><b>" + (p.ai ? p.revN : p.rejN) + "</b></div><div><span>Token</span><b>" + (p.ai ? fmtK(p.tok) : "—") + "</b></div>");
  if (PF.stc !== STC[s[1]]) { PF.stc = STC[s[1]]; PF.st.style.color = PF.stc; }
  setTxt(PF.st, "● " + s[0] + (p.task === "meet" && MT ? " · " + MT.title : ""));
  setHTML(PF.now, it ? taskHTML(it) : (p.isChief ? "拆解任务 · 派单 · 终审" : "暂无任务"));
  var bc = it ? it.P.c : "var(--hi)"; if (PF.bc !== bc) { PF.bc = bc; PF.bar.style.setProperty("--c", bc); }
  setW(PF.bi, p.cur ? p.cur.prog : 0);
  var qs = p.q.slice(it === p.q[0] ? 1 : 0, 2);
  setHTML(PF.q, (qs.length ? qs.map(function (x) { return "<div><span>排队</span><span>" + taskHTML(x) + "</span></div>"; }).join("") : "<div><span>排队</span><span>无</span></div>") + (p.inb ? "<div><span>在途</span><span>" + p.inb + " 份文件正在送来</span></div>" : ""));
  setHTML(PF.hi, p.hist.length ? p.hist.slice(0, 4).map(function (x) { return "<div><span>" + x.t + "</span><span>" + esc(x.s) + "</span></div>"; }).join("") : "<div><span>—</span><span>暂无记录</span></div>");
}
function todSync() {
  var hh = Math.floor(TOD.h), mm = Math.floor((TOD.h - hh) * 60);
  setTxt($("todT"), pad(hh, 2) + ":" + pad(mm, 2));
  setTxt($("todIc"), E.night < 0.5 ? "☀" : "☾");
  var ph = E.night > 0.6 ? "夜晚" : (E.night > 0.15 || (TOD.h > 16.8 && TOD.h < 19.5) || (TOD.h > 5 && TOD.h < 7) ? "黄昏" : "白天");
  setTxt($("todL"), TOD.names[TOD.mode] + " · " + ph);
}

/* =====================================================================
   25. 弹窗：新增任务（交给 Chief）/ 人员管理（HR 增删查改）
   ===================================================================== */
var MODAL = null;
function openModal(id, focusEl) {
  closeModal(true);
  var m = $(id); m.hidden = false; MODAL = m;
  if (view === 1) view = 0;
  setTimeout(function () { var f = focusEl || m.querySelector("input,button"); if (f) try { f.focus(); } catch (e) { /* 忽略 */ } }, 30);
}
function closeModal(silent) { if (!MODAL) return; MODAL.hidden = true; if (MODAL.id === "mdlHR") HRUI.open = false; MODAL = null; }
[$("mdlTask"), $("mdlHR")].forEach(function (m) {
  m.addEventListener("click", function (e) { if (e.target === m || closest(e, "[data-close]")) closeModal(); });
});
/* ---------- 新增任务 ---------- */
var TK = { tpl: 0, custom: [], n: 4, pri: 0 };
var CUSTOM_STAGE = ["需求梳理", "设计", "推广方案", "运营执行", "开发", "人事处理", "财务核算", "法务审查"];
function customStages(list) { return list.map(function (d) { return d < 0 ? [-1, "Chief 终审", 2, 3, 5] : [d, CUSTOM_STAGE[d], 1, 7, 11]; }); }
function tkStages() { return TPL[TK.tpl].S || customStages(TK.custom); }
function tkRender() {
  setHTML($("tkTpl"), TPL.map(function (t, i) { return '<button class="chip' + (i === TK.tpl ? " on" : "") + '" type="button" data-tpl="' + i + '">' + t.zh + "</button>"; }).join(""));
  var cu = !TPL[TK.tpl].S; $("tkCustomF").hidden = !cu;
  if (cu) setHTML($("tkDepts"), DEPTS.map(function (D, k) { return '<button class="chip" type="button" data-dd="' + k + '" style="--c:' + D.c + '"><i></i>' + D.zh + "</button>"; }).join("") + '<button class="chip" type="button" data-dd="-1" style="--c:' + CHIEF_C + '"><i></i>Chief 终审</button>');
  var S = tkStages(), h = "";
  S.forEach(function (s, i) {
    var rp = rvOf({ d: s[0], rv: s[2] }), rv = !rp ? "" : (rp === CHIEF ? (s[0] >= 0 && s[2] === 1 ? "负责人空缺 → Chief 审" : "Chief 审") : esc(rp.n) + (rp.ai ? "（AI）" : "（真人）") + (rp.head ? "审" : "代审"));
    h += (i ? '<span class="ar">→</span>' : "") + '<span class="st" style="--c:' + dColor(s[0]) + '">' + (s[0] >= 0 ? DEPTS[s[0]].zh + " · " : "") + s[1] + (rv ? '<em class="' + (rv.indexOf("真人") >= 0 ? "h" : (rv.indexOf("Chief") === 0 ? "c" : "")) + '">' + rv + "</em>" : "") + (cu ? '<button type="button" data-rm="' + i + '" aria-label="删除这一步">×</button>' : "") + "</span>";
  });
  setHTML($("tkChain"), h || '<span class="none">先点上面的部门，按顺序加入流程</span>');
  var mx = Math.min(8, TPL[TK.tpl].parts.length), rg = $("tkN");
  if (+rg.max !== mx) rg.max = String(mx);
  if (TK.n > mx) { TK.n = mx; rg.value = String(mx); }
  setTxt($("tkNv"), String(TK.n));
  [].forEach.call($("tkPri").querySelectorAll("button"), function (b) { setCls(b, +b.getAttribute("data-v") === TK.pri ? "on" : ""); });
}
function openTask() {
  TK = { tpl: 0, custom: [], n: 4, pri: 0 };
  $("tkName").value = ""; $("tkN").value = "4"; $("tkErr").hidden = true;
  tkRender(); openModal("mdlTask", $("tkName"));
  say(CHIEF, "有什么任务交给我？", "ai", 2);
}
$("tkTpl").addEventListener("click", function (e) { var b = closest(e, "[data-tpl]"); if (!b) return; TK.tpl = +b.getAttribute("data-tpl"); tkRender(); });
$("tkDepts").addEventListener("click", function (e) { var b = closest(e, "[data-dd]"); if (!b) return; if (TK.custom.length >= 5) { $("tkErr").hidden = false; setTxt($("tkErr"), "自定义流程最多 5 步"); return; } TK.custom.push(+b.getAttribute("data-dd")); $("tkErr").hidden = true; tkRender(); });
$("tkChain").addEventListener("click", function (e) { var b = closest(e, "[data-rm]"); if (!b) return; TK.custom.splice(+b.getAttribute("data-rm"), 1); tkRender(); });
$("tkN").addEventListener("input", function () { TK.n = +$("tkN").value; setTxt($("tkNv"), String(TK.n)); });
$("tkPri").addEventListener("click", function (e) { var b = closest(e, "button"); if (!b) return; TK.pri = +b.getAttribute("data-v"); tkRender(); });
$("tkName").addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); submitTask(); } });
$("tkGo").addEventListener("click", submitTask);
function createTask(name, tplIdx, stages, n, urgent) {
  var t = TPL[tplIdx]; n = Math.min(n, t.parts.length);
  var parts = shuffle(t.parts.slice()).slice(0, n);
  if (!t.S) parts = t.parts.slice(0, n);
  var P = mkProject({ zh: name, tpl: tplIdx, S: stages, T: parts, user: true, urgent: urgent });
  projDirty = true;
  var first = P.S[0], to = first.d >= 0 ? DEPTS[first.d].zh : "Chief";
  log("小枢", CHIEF_C, "收到新任务 · " + P.id + " " + name + " · 拆成 " + n + " 项 · " + P.S.map(function (s) { return s.d >= 0 ? DEPTS[s.d].zh.replace("行政部", "") : "Chief"; }).join(" → ") + (urgent ? " · 加急" : ""), "g", { j: P.id, d: [], p: [CHIEF.id] });
  say(CHIEF, "收到！拆成 " + n + " 项，先交给" + to, "ai", 3);
  CHIEF.talkT = 2;
  for (var i = 0; i < 3; i++) RINGS.push({ p: [HOLO[0], 0.7 + i * 0.35, HOLO[1]], c: P.col, t0: now + i * 0.2, r: 1.8 });
  if (urgent) ST.dispT = 0;
  return P;
}
function submitTask() {
  var name = $("tkName").value.replace(/\s+/g, " ").trim(), err = $("tkErr");
  if (!name) { err.hidden = false; setTxt(err, "先给任务起个名字，比如「新人引导体验优化」"); $("tkName").focus(); return; }
  var S = tkStages();
  if (!S.length) { err.hidden = false; setTxt(err, "自定义流程至少要选一个部门"); return; }
  var P = createTask(name, TK.tpl, S, TK.n, TK.pri === 1);
  closeModal();
  toast("已交给 Chief：" + name + "（拆成 " + P.N + " 项）");
  goView("pj", P);
}
/* ---------- 人员管理 ---------- */
var HRUI = { open: false, dept: -1, q: "", form: null, conf: null, look: null, type: 1 };
function hrRender() {
  var liv = living(), nAI = 0, nHu = 0;
  liv.forEach(function (p) { if (p.ai) nAI++; else nHu++; });
  setTxt($("hrMeta"), liv.length + " 人 · " + nAI + " 个 Agent · " + nHu + " 位真人督办 · 人事部负责人：" + (headOf(HRD) ? headOf(HRD).n : "空缺"));
  if (HRUI.form) return;
  setHTML($("hrDept"), '<button class="chip' + (HRUI.dept === -1 ? " on" : "") + '" type="button" data-hd="-1">全部 ' + liv.length + "</button>" + DEPTS.map(function (D, k) { return '<button class="chip' + (HRUI.dept === k ? " on" : "") + '" type="button" data-hd="' + k + '" style="--c:' + D.c + '"><i></i>' + D.zh + " " + members(k).length + "</button>"; }).join(""));
  var q = HRUI.q.toLowerCase(), h = "";
  var groups = HRUI.dept >= 0 ? [HRUI.dept] : [-1].concat(DEPTS.map(function (D, k) { return k; }));
  groups.forEach(function (k) {
    var ps = (k < 0 ? [CHIEF] : members(k).sort(function (a, b) { return (b.head ? 1 : 0) - (a.head ? 1 : 0); })).filter(function (p) { return !q || (p.n + p.role + p.skills.join("")).toLowerCase().indexOf(q) >= 0; });
    if (!ps.length && (q || k < 0)) return;
    h += '<div class="hr-dh" style="--c:' + dColor(k) + '">' + (k < 0 ? "Chief" : DEPTS[k].zh) + "<em>" + (k < 0 ? "总指挥 · 不可移出" : seatCount(k) + " / " + (DEPTS[k].cap + 1) + " 工位 · " + DEPTS[k].fn) + "</em></div>";
    if (!ps.length) h += '<div class="hr-empty">暂无成员</div>';
    ps.forEach(function (p) {
      if (HRUI.conf === p) {
        var n = p.q.length + (p.cur ? 1 : 0) + (p.carry ? 1 : 0);
        h += '<div class="hr-r cf"><span class="cft">确认让 <b>' + esc(p.n) + "</b> 离开" + DEPTS[p.d].zh + "？" + (n ? "手上 " + n + " 项工作会移交给同部门的同事" + (members(p.d).filter(function (x) { return x.ai && x !== p; }).length ? "" : "（部门里没有别的 Agent，会先交给 Chief 代办）") + "。" : "手上没有工作。") + (p.head ? "负责人离开后，审核交给部门里的其他真人督办，没有就交给 Chief。" : "") + '</span><span class="ac"><button class="btn sm dng" type="button" data-hr-ok="' + p.id + '">确认移出</button><button class="btn sm" type="button" data-hr-no="1">取消</button></span></div>';
        return;
      }
      h += '<div class="hr-r" style="' + avStyle(p) + '"><span class="nm"><i class="av"></i><b>' + esc(p.n) + "</b>" + badge(p) + (p.head ? "<em>负责人</em>" : "") + '</span><span class="dp">' + dName(p.d) + '</span><span class="ro">' + esc(p.role) + (p.skills.length ? " · " + esc(p.skills.join("、")) : "") + '</span><span class="st">' + pStatus(p)[0] + '</span><span class="ac">' +
        (p.isChief ? '<button class="btn sm" type="button" data-act="task">派任务</button>' : '<button class="btn sm" type="button" data-hr-edit="' + p.id + '">编辑</button><button class="btn sm dng" type="button" data-hr-del="' + p.id + '">移出</button>') + "</span></div>";
    });
  });
  setHTML($("hrTb"), h || '<div class="hr-empty">没有找到匹配的成员</div>');
}
function hrShowList() { HRUI.form = null; $("hrList").hidden = false; $("hrForm").hidden = true; $("hrFoot").hidden = false; $("hfFoot").hidden = true; setHTML($("hrTitle"), "人员管理 · <u>HR</u>"); hrRender(); }
function openHR(dept) {
  HRUI.open = true; HRUI.dept = dept === undefined ? -1 : dept; HRUI.q = ""; HRUI.conf = null; $("hrQ").value = "";
  hrShowList(); openModal("mdlHR", $("hrQ"));
  var hh = headOf(HRD); if (hh) say(hh, rpick(["要招人还是调岗？", "人员的事交给我", "我来安排"]), hh.ai ? "ai" : "g", 2);
}
function lookSync() {
  var L0 = HRUI.look, av = $("hfAv");
  av.style.setProperty("--sh", L0.sc); av.style.setProperty("--hc", L0.hc); av.style.setProperty("--ring", HRUI.type ? "var(--ai)" : "var(--hu)");
  var d = +$("hfDept").value, p = HRUI.form === "new" ? null : HRUI.form, D = DEPTS[d], free = 0;
  D.seats.forEach(function (s) { if (!s.own || s.own === p) free++; });
  var hd = headOf(d), wantHead = $("hfHead").checked, note = D.zh + " · 空工位 " + free + " 个";
  if (wantHead && hd && hd !== p) note += " · 现任负责人 " + hd.n + " 会改为普通成员";
  setTxt($("hfSeat"), note);
}
function openForm(p) {
  HRUI.form = p || "new";
  $("hrList").hidden = true; $("hrForm").hidden = false; $("hrFoot").hidden = true; $("hfFoot").hidden = false; $("hfErr").hidden = true;
  var dsel = $("hfDept"); dsel.innerHTML = DEPTS.map(function (D, k) { return '<option value="' + k + '">' + D.zh + "（" + seatCount(k) + "/" + (D.cap + 1) + "）</option>"; }).join("");
  if (p) {
    $("hfName").value = p.n; $("hfRole").value = p.role; $("hfSk").value = p.skills.join("、"); dsel.value = String(p.d); $("hfHead").checked = !!p.head;
    HRUI.type = p.ai ? 1 : 0; HRUI.look = { hs: p.hs, hc: p.hc, sc: p.sc, skin: p.skin };
    setHTML($("hrTitle"), "编辑成员 · <u>" + esc(p.n) + "</u>");
  } else {
    var d0 = HRUI.dept >= 0 ? HRUI.dept : 4;
    $("hfName").value = ""; $("hfRole").value = ""; $("hfSk").value = ""; dsel.value = String(d0); $("hfHead").checked = false;
    HRUI.type = 1; HRUI.look = randomLook();
    setHTML($("hrTitle"), "新增成员 · <u>入职</u>");
  }
  [].forEach.call($("hfType").querySelectorAll("button"), function (b) { setCls(b, +b.getAttribute("data-v") === HRUI.type ? "on" : ""); });
  lookSync();
  setTimeout(function () { try { $("hfName").focus(); } catch (e) { /* 忽略 */ } }, 30);
}
function hfSave() {
  var p = HRUI.form === "new" ? null : HRUI.form, d = +$("hfDept").value, ai = HRUI.type === 1;
  var role = $("hfRole").value.trim() || (ai ? DEPTS[d].zh.replace("部", "").replace("行政", "") + " Agent" : DEPTS[d].zh.replace("部", "") + "督办");
  var o = { n: $("hfName").value.replace(/\s+/g, "").trim(), role: role, d: d, ai: ai, head: $("hfHead").checked, skills: $("hfSk").value.split(/[、,，\s]+/).filter(function (x) { return x; }).slice(0, 5),
    hs: HRUI.look.hs, hc: HRUI.look.hc, sc: HRUI.look.sc, skin: HRUI.look.skin };
  var err = p ? hrEdit(p, o) : hrAdd(o);
  if (err) { $("hfErr").hidden = false; setTxt($("hfErr"), err); return; }
  toast(p ? "已更新 " + o.n : o.n + " 已入职 " + DEPTS[d].zh + "，正从大门走进来");
  HRUI.dept = d; hrShowList();
}
$("hrDept").addEventListener("click", function (e) { var b = closest(e, "[data-hd]"); if (!b) return; HRUI.dept = +b.getAttribute("data-hd"); HRUI.conf = null; hrRender(); });
$("hrQ").addEventListener("input", function () { HRUI.q = $("hrQ").value.trim(); hrRender(); });
$("hrAdd").addEventListener("click", function () { openForm(null); });
$("hfCancel").addEventListener("click", function () { hrShowList(); });
$("hfSave").addEventListener("click", hfSave);
$("hfLook").addEventListener("click", function () { HRUI.look = randomLook(); lookSync(); });
$("hfType").addEventListener("click", function (e) { var b = closest(e, "button"); if (!b) return; HRUI.type = +b.getAttribute("data-v"); [].forEach.call($("hfType").querySelectorAll("button"), function (x) { setCls(x, +x.getAttribute("data-v") === HRUI.type ? "on" : ""); }); lookSync(); });
$("hfDept").addEventListener("change", lookSync); $("hfHead").addEventListener("change", lookSync);
$("hfName").addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); hfSave(); } });
$("hrTb").addEventListener("click", function (e) {
  var b;
  if ((b = closest(e, "[data-hr-edit]"))) { var p = findP(b.getAttribute("data-hr-edit")); if (p) openForm(p); }
  else if ((b = closest(e, "[data-hr-del]"))) { HRUI.conf = findP(b.getAttribute("data-hr-del")); hrRender(); }
  else if ((b = closest(e, "[data-hr-ok]"))) { var q = findP(b.getAttribute("data-hr-ok")); HRUI.conf = null; if (q) { var nm = q.n, er = hrRemove(q); toast(er || nm + " 已移出，正在交接后离开"); } hrRender(); }
  else if (closest(e, "[data-hr-no]")) { HRUI.conf = null; hrRender(); }
});
$("hrReset").addEventListener("click", function () {
  var b = $("hrReset");
  if (b.__armed) { b.__armed = false; b.textContent = "恢复默认名单"; hrReset(); HRUI.dept = -1; hrRender(); toast("名单已恢复默认"); return; }
  b.__armed = true; b.textContent = "再点一次确认恢复（当前名单会被替换）";
  setTimeout(function () { b.__armed = false; b.textContent = "恢复默认名单"; }, 4000);
});
$("pfl").addEventListener("click", function (e) {
  var b;
  if ((b = closest(e, "[data-hr-edit]"))) { var p = findP(b.getAttribute("data-hr-edit")); if (p) { openHR(p.d); openForm(p); } }
  else if ((b = closest(e, "[data-hr-del]"))) { var q = findP(b.getAttribute("data-hr-del")); if (q) { openHR(q.d); HRUI.conf = q; hrRender(); } }
  else if (closest(e, '[data-act="task"]')) openTask();
});

/* =====================================================================
   26. 渲染循环
   ===================================================================== */
var last = 0, uiAcc = 0, fpsAcc = 0, fpsN = 0, todAcc = 1, FC = 0;
var GOV = { t: 0, acc: 0, n: 0, bad: 0, lvl: 0, run: 0 };
function govern(raw) {
  if (!Q.auto) return;
  GOV.run += raw; if (GOV.run < 5) return;
  GOV.acc += raw; GOV.n++; GOV.t += raw;
  if (GOV.t < 2.5) return;
  var avg = GOV.acc / GOV.n; GOV.t = 0; GOV.acc = 0; GOV.n = 0;
  GOV.bad = avg > 1 / 34 ? GOV.bad + 1 : 0;
  if (GOV.bad >= 2 && GOV.lvl < 3) { GOV.bad = 0; GOV.lvl++; if (GOV.lvl === 1) Q.ms = false; else if (GOV.lvl === 2) { Q.dpr = Math.max(0.6, L.dpr * 0.75); layout(); } else Q.sh = false; }
}
function frame(ts) {
  if (destroyed) return;
  rafId = requestAnimationFrame(frame);
  if (host.hidden || document.hidden || !L.ok) { last = 0; return; }
  var raw = last ? (ts - last) / 1000 : 0.016, dt = Math.min(0.05, raw);
  last = ts; now += dt; FC++;
  if (raw < 1) govern(raw);
  step(dt);
  if (gl) { buildScene(now, dt); shadowSetup(); drawAll(now); }
  placeHud();
  uiAcc += dt; fpsAcc += dt; fpsN++; todAcc += dt;
  if (uiAcc > 0.2) { uiAcc = 0; updatePanels(); }
  if (fpsAcc > 0.5) { setTxt($("sFps"), String(Math.round(fpsN / fpsAcc))); fpsAcc = 0; fpsN = 0; }
  if (todAcc > 1) { todAcc = 0; todSync(); }
}
function step(dt) {
  updateSim(dt);
  envUpdate();
  camUpdate(dt);
  var close = V.k === "ps" ? 1 : (V.k === "dp" ? 0.6 : 0);
  E.tilt = (L.narrow ? 0.45 : 0.85) * (1 - 0.55 * close); E.band = lerp(0.2, 0.28, close);
  E.fogS = lerp(80, 50, close); E.fogD = lerp(0.006, 0.008, close);
  E.shR = V.k === "ps" ? 7 : (V.k === "dp" ? 12 : 25); E.shN = lerp(0.07, 0.02, close); E.shB = lerp(0.0007, 0.00035, close);
}
function shadowSetup() {
  var c = V.k === "ov" || V.k === "pj" ? [0, 0, 0] : CV.t, R = E.shR || 25, d = E.sunDir, up = Math.abs(d[1]) > 0.97 ? [0, 0, 1] : [0, 1, 0];
  SMAT = m4mul(m4ortho(-R, R, -R, R, 20, 180), m4look(v3add(c, v3sc(d, 90)), c, up));
  E.shOn = Q.sh && E.sunC[0] + E.sunC[1] > 0.05;
}

/* =====================================================================
   27. 交互：拖动转镜头 / 滚轮缩放 / 双击复位 / 点 Chief 派任务 / 点人事部的人管人员 / 点人看档案 / 点部门进部门 / Esc 返回
   ===================================================================== */
var dragging = false, lastX = 0, lastY = 0, moved = 0;
function rectOf(pts) { var x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9, ok = false, ws = 0; pts.forEach(function (p) { var q = proj(p); if (!q) return; ok = true; x0 = Math.min(x0, q[0]); x1 = Math.max(x1, q[0]); y0 = Math.min(y0, q[1]); y1 = Math.max(y1, q[1]); ws += q[2]; }); return ok ? [x0, y0, x1, y1, ws / pts.length] : null; }
function boxPts(x0, x1, y0, y1, z0, z1) { var o = []; [x0, x1].forEach(function (x) { [y0, y1].forEach(function (y) { [z0, z1].forEach(function (z) { o.push([x, y, z]); }); }); }); return o; }
var ROOMBOX = [[-6, -2.2, 6.2, 12.5], [2.2, 6, 6.2, 12.5], [-2.2, 2.2, 6.2, 12.5], [GM.x0, GM.x1, GM.z0, GM.z1]];
function pick(px, py) {
  var best = null, bd = 1e9;
  PEOPLE.forEach(function (p) {
    if (p.gone) return;
    var a = proj(headPt(p, 0)), b = proj(headPt(p, 0.3)); if (!a || !b) return;
    var r = Math.max(p.isChief ? 14 : 9, Math.abs(a[1] - b[1]) * 1.25), d = hyp(px - a[0], py - (a[1] + r * 0.4));
    if (d < r * 1.5 && d < bd) { bd = d; best = { p: p }; }
  });
  if (best) return best;
  var rh = rectOf(boxPts(-1.4, 1.4, 0, 1.5, HOLO[1] - 1.4, HOLO[1] + 0.6)); if (rh && px > rh[0] && px < rh[2] && py > rh[1] && py < rh[3]) return { chief: true };
  DEPTS.forEach(function (D, k) { var R = D.R, r = rectOf(boxPts(R.x0, R.x1, 0, 0.9, R.z0, R.z1)); if (r && px > r[0] && px < r[2] && py > r[1] && py < r[3] && (!best || r[4] < best.dd)) best = { d: k, dd: r[4] }; });
  if (best) return best;
  ROOMBOX.forEach(function (B, i) { var r = rectOf(boxPts(B[0], B[1], 0, 1.0, B[2], B[3])); if (r && px > r[0] && px < r[2] && py > r[1] && py < r[3] && (!best || r[4] < best.dd)) best = { r: i, dd: r[4] }; });
  return best;
}
function roomAct(i) {
  if (i === 0) { if (MT) { if (MT.P) goView("pj", MT.P); else if (MT.ppl[0]) goView("ps", MT.ppl[0]); } return; }
  if (i === 1) { var lp = PEOPLE.filter(function (p) { return p.task === "lounge" && !p.gone; }); if (lp.length) goView("ps", lp[0]); return; }
  if (i === 2) openHR();
  if (i === 3) openTask();
}
function personAct(p) {
  if (p.isChief) { openTask(); return; }
  if (p.d === HRD) { openHR(); return; }
  goView("ps", p);
}
function onPick(pk) { if (!pk) return; if (view === 1) view = 0; if (pk.p) personAct(pk.p); else if (pk.chief) openTask(); else if (pk.d !== undefined) goView("dp", pk.d); else if (pk.r !== undefined) roomAct(pk.r); syncButtons(); }
function hostXY(e) { var r = host.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
glc.addEventListener("pointerdown", function (e) { dragging = true; lastX = e.clientX; lastY = e.clientY; moved = 0; glc.classList.add("drag"); try { glc.setPointerCapture(e.pointerId); } catch (er) { /* 忽略 */ } });
var hoverT = 0;
glc.addEventListener("pointermove", function (e) {
  if (!dragging) { var t0 = performance.now(); if (t0 - hoverT < 90) return; hoverT = t0; var q = hostXY(e); glc.classList.toggle("pick", !!pick(q[0], q[1])); return; }
  var dx = e.clientX - lastX, dy = e.clientY - lastY; lastX = e.clientX; lastY = e.clientY; moved += Math.abs(dx) + Math.abs(dy);
  DRG.yaw = clamp(DRG.yaw - dx * 0.005, -1.6, 1.6); DRG.pitch = clamp(DRG.pitch + dy * 0.004, -0.5, 0.6);
});
function endDrag(e) { if (!dragging) return; dragging = false; glc.classList.remove("drag"); if (e && e.type === "pointerup" && moved < 6) { var q = hostXY(e); onPick(pick(q[0], q[1])); } }
glc.addEventListener("pointerup", endDrag); glc.addEventListener("pointercancel", endDrag);
function resetView() { DRG.yaw = 0; DRG.pitch = 0; DRG.zoom = 1; syncButtons(); }
glc.addEventListener("dblclick", resetView);
glc.addEventListener("wheel", function (e) { e.preventDefault(); DRG.zoom = clamp(DRG.zoom * Math.exp(e.deltaY * 0.0012), 0.35, 1.8); }, { passive: false });
function closest(e, sel) { return e.target && e.target.closest ? e.target.closest(sel) : null; }
function bindClick(id, sel, f) { if ($(id)) $(id).addEventListener("click", function (e) { var b = closest(e, sel); if (b) { if (view === 1) view = 0; f(b, e); } }); }
bindClick("dl", ".dr", function (b) { goView("dp", +b.getAttribute("data-d")); });
bindClick("pl", ".pr", function (b) { var P = findPJ(b.getAttribute("data-pid")); if (P) goView("pj", P); });
bindClick("sbd", "[data-pid],h4[data-d]", function (b) { if (b.hasAttribute("data-pid")) { var p = findP(b.getAttribute("data-pid")); if (p) goView("ps", p); } else { var d = +b.getAttribute("data-d"); if (d >= 0) goView("dp", d); else goView("ps", CHIEF); } });
bindClick("mtb", ".mtr", function (b) { var p = findP(b.getAttribute("data-pid")); if (p) goView("ps", p); });
bindClick("labels", ".lab", function (b, e) {
  if (closest(e, "[data-hr]")) { openHR(); return; }
  if (closest(e, "[data-task]") || b.hasAttribute("data-chief")) { openTask(); return; }
  if (b.hasAttribute("data-d")) goView("dp", +b.getAttribute("data-d")); else roomAct(+b.getAttribute("data-r"));
});
bindClick("tags", ".tg", function (b) { var p = findP(b.getAttribute("data-pid")); if (p) goView("ps", p); });
bindClick("sel", "button", function (b) { if (b.hasAttribute("data-d")) goView("dp", +b.getAttribute("data-d")); else if (b.hasAttribute("data-pid") && /^P/.test(b.getAttribute("data-pid"))) { var P = findPJ(b.getAttribute("data-pid")); if (P) goView("pj", P); } else { var p = findP(b.getAttribute("data-pid")); if (p) goView("ps", p); } });
bindClick("vt", "button", function (b) { var v = b.getAttribute("data-v"); goView(v, v === "dp" ? V.d : (v === "pj" ? (V.P && PROJS.indexOf(V.P) >= 0 ? V.P : PROJS[0]) : (v === "ps" ? (V.p && !V.p.gone ? V.p : CHIEF) : 0))); });
bindClick("mCh", "button", function (b) { goView("dp", +b.getAttribute("data-d")); });
bindClick("mPj", "button", function (b) { var P = findPJ(b.getAttribute("data-pid")); if (P) goView("pj", P); });
[$("dl"), $("pl"), $("sbd"), $("mtb")].filter(function (b) { return b; }).forEach(function (box) { box.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { var b = closest(e, "[role=button]"); if (b) { e.preventDefault(); b.click(); } } }); });
function cycle(dir) {
  if (V.k === "dp" || V.k === "ov") goView("dp", V.k === "ov" ? (dir > 0 ? 0 : ND - 1) : (V.d + dir + ND) % ND);
  else if (V.k === "pj") { var i = PROJS.indexOf(V.P); goView("pj", PROJS[(i + dir + PROJS.length) % PROJS.length]); }
  else if (V.p) { var grp = V.p.isChief ? [CHIEF] : members(V.p.d), ix = grp.indexOf(V.p); if (grp.length) goView("ps", grp[(ix + dir + grp.length) % grp.length]); }
}
function act(name) {
  if (name === "mode") { view = (view + 1) % VIEWS.length; if (view === 1) { TOUR.i = -1; TOUR.t = 0; } else startFly(0); }
  else if (name === "home") { if (view === 1) view = 0; goView("ov", 0); }
  else if (name === "back") { if (view === 1) view = 0; goBack(); }
  else if (name === "prev" || name === "next") { if (view === 1) view = 0; cycle(name === "next" ? 1 : -1); }
  else if (name === "tod") { TOD.mode = (TOD.mode + 1) % TOD.names.length; envUpdate(); todSync(); }
  else if (name === "task") openTask();
  else if (name === "hr") openHR(V.k === "dp" ? V.d : undefined);
  else if (name === "full") {
    try {
      if (document.fullscreenElement) {
        document.exitFullscreen().catch(function () {});
      } else {
        var el = host.parentElement || host;
        if (el.requestFullscreen) {
          el.requestFullscreen().catch(function () {
            var b = SR.querySelector('[data-f="full"]');
            if (b) { b.textContent = "不支持"; setTimeout(function () { b.textContent = "全屏"; }, 1500); }
          });
        }
      }
    } catch (e) { /* 部分窗口不允许全屏 */ }
  }
  syncButtons();
}
SR.addEventListener("click", function (e) {
  var b = closest(e, "[data-act]");
  if (b && !closest(e, "#pfl")) act(b.getAttribute("data-act"));



  var ex = closest(e, '[data-action="open-exchange"]');
  if (ex) {
    if (bridge && bridge.onOpenExchange) bridge.onOpenExchange();
    return;
  }

  var presetBtn = closest(e, "[data-preset-idx]");
  if (presetBtn) {
    var pidx = +presetBtn.getAttribute("data-preset-idx");
    var PRESETS = [
      { name: "单文件FinOps算力成本与ROI计算器", tpl: 0, pri: 1, n: 4 },
      { name: "企业级Web中后台系统", tpl: 1, pri: 1, n: 6 },
      { name: "全网行业竞品深度调研", tpl: 3, pri: 0, n: 3 },
      { name: "广告法全量红线安全审计", tpl: 2, pri: 0, n: 2 }
    ];
    var pInfo = PRESETS[pidx];
    if (pInfo) {
      var nameInp = SR.getElementById("tkName");
      if (nameInp) nameInp.value = pInfo.name;
      TK.tpl = pInfo.tpl;
      TK.pri = pInfo.pri;
      TK.n = pInfo.n;
      tkSync();
    }
    return;
  }

  var revClick = closest(e, "#kRevT") || closest(e, "#kRev");
  if (revClick) {
    if (bridge && bridge.onNavigateTab) bridge.onNavigateTab("approvals");
    return;
  }
});
function onFsChange() {
  var b = SR.querySelector('[data-f="full"]');
  if (b) b.textContent = document.fullscreenElement ? "退出全屏" : "全屏";
}
document.addEventListener("fullscreenchange", onFsChange);

function onKeyDown(e) {
  if (host.hidden || destroyed) return;
  var t0 = e.composedPath ? e.composedPath()[0] : e.target, tag0 = (t0 && t0.tagName) || "";
  if (MODAL) { if (e.key === "Escape") { if (MODAL.id === "mdlHR" && HRUI.form) hrShowList(); else closeModal(); e.stopPropagation(); } return; }
  if (/INPUT|TEXTAREA|SELECT/.test(tag0) || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "v" || e.key === "V") act("mode");
  else if (e.key === "n" || e.key === "N") act("tod");
  else if (e.key === "c" || e.key === "C") act("task");
  else if (e.key === "h" || e.key === "H") act("hr");
  else if (e.key === "p" || e.key === "P") { if (view === 1) view = 0; if (PROJS.length) goView("pj", V.k === "pj" ? PROJS[(PROJS.indexOf(V.P) + 1) % PROJS.length] : (V.P && PROJS.indexOf(V.P) >= 0 ? V.P : PROJS[0])); }
  else if (e.key === "Escape") { if (V.k === "ps") act("back"); else if (V.k !== "ov") act("home"); }
  else if (e.key === "ArrowRight") act("next");
  else if (e.key === "ArrowLeft") act("prev");
  else if (e.key === "0") act("home");
  else if (/^[1-8]$/.test(e.key)) { if (view === 1) view = 0; goView("dp", +e.key - 1); }
}
document.addEventListener("keydown", onKeyDown);

/* ---------- 开场：项目已推进到不同阶段，桌上有待办，几个人已经在干活 ---------- */
(function warmStart() {
  PRESET.forEach(function (o) {
    var P = mkProject({ zh: o.zh, tpl: TPLK[o.tpl], T: o.T, preset: true });
    P.newAt = -99;
    var nDone = Math.floor(P.N * o.f * 0.72), wip = Math.min(wipOf(P) + 1, P.N - nDone), i;
    for (i = 0; i < nDone; i++) { P.items[i].st = "fin"; P.items[i].s = P.ns; P.items[i].finAt = -100 + i; P.done++; }
    for (i = nDone; i < nDone + wip; i++) {
      var it = P.items[i], s = clamp(Math.round(o.f * P.ns * 1.2) - (i - nDone) % P.ns, 0, P.ns - 1), S = P.S[s];
      it.s = s; it.born = -rr(40, 160);
      if (s > 0 && P.S[s - 1].d >= 0) it.wk = pickWorker(P.S[s - 1], null);
      var rv = rvOf(S), kind = "work", to;
      if (S.d === -1) { kind = "rev"; to = CHIEF; }
      else if (rv && Math.random() < 0.35) { kind = "rev"; to = rv; it.wk = pickWorker(S, null); }
      else to = pickWorker(S, null);
      it.kind = kind; it.who = to; it.st = "q"; to.q.push(it);
    }
  });
  PEOPLE.forEach(function (p) { if (p.q.length) { startTask(p); p.cur.prog = rr(0.05, 0.75); } });
  var r = mulberry32(9), tok = 0;
  PEOPLE.forEach(function (p) {
    if (p.ai) { p.doneN = 2 + Math.floor(r() * 8); p.tok = 18000 + r() * 70000; tok += p.tok; p.revN = p.head || p.isChief ? 3 + Math.floor(r() * 7) : 0; }
    else p.revN = 4 + Math.floor(r() * 9);
    var hh = 9 + Math.floor(r() * 2);
    var mine = PROJS.filter(function (P0) { return p.isChief || P0.S.some(function (S0) { return S0.d === p.d; }); });
    for (var k = 0; mine.length && k < 2 + Math.floor(r() * 2); k++) { var P = mine[Math.floor(r() * mine.length)]; p.hist.push({ t: pad(hh + Math.floor(k / 2), 2) + ":" + pad(Math.floor(r() * 60), 2), s: (p.ai ? "完成 " : "审核通过 ") + P.id + " · " + P.T[Math.floor(r() * P.N)] }); }
    p.hist.reverse();
  });
  ST.tok = tok; ST.done = 21; ST.stages = 138; ST.rev = 39; ST.rej = 4;
  for (var t = 0; t < 12; t++) ST.lead.push(80 + r() * 90);
  var hrh = headOf(HRD) || CHIEF, nA = 0; living().forEach(function (p) { if (p.ai) nA++; });
  log(hrh.n, pcol(hrh), "人员日报 · 在岗 " + living().length + " 人（Agent " + nA + " · 真人督办 " + (living().length - nA) + "）", "", { d: [HRD], p: [hrh.id] });
  log("小枢", CHIEF_C, "HQ 10 自动运行 · " + living().length + " 名成员在线 · 点我可以派新任务", "", { d: [], p: [CHIEF.id] });
})();
setTxt($("clock"), hms()); clockTimer = setInterval(function () { if (!host.hidden && !destroyed) setTxt($("clock"), hms()); }, 1000);
buildStaff(); buildProjRows(); buildBottom(); syncButtons();
envUpdate(); todSync();
var roT = 0;
function onWinResize() { clearTimeout(roT); roT = setTimeout(layout, 60); }
if (window.ResizeObserver) {
  roInst = new ResizeObserver(onWinResize);
  roInst.observe(host);
} else {
  window.addEventListener("resize", onWinResize);
}
if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { layout(); });
layout();
updatePanels();
// 调试用：document.getElementById('host-hq').__go('dp', 1) / .__tod(3) / .__st()
function byName(n) { if (typeof n !== "string") return n; for (var i = 0; i < PEOPLE.length; i++) if (PEOPLE[i].n === n) return PEOPLE[i]; return null; }
host.__dbg = function () { var r = []; camUpdate(0.016); [updatePanels, placeHud, function () { if (gl) buildScene(now, 0.016); }].forEach(function (f) { try { f(); r.push("ok"); } catch (e) { r.push(String(e) + " " + (e.stack || "").split("\n")[1]); } }); return r; };
host.__go = function (k, i) { goView(k, k === "pj" ? (typeof i === "string" ? findPJ(i) : PROJS[i || 0]) : (k === "ps" ? byName(i) : i)); snapCam(); syncButtons(); return V.k; };
host.__view = function (v) { view = v; snapCam(); syncButtons(); };
host.__tod = function (m) { TOD.mode = m; envUpdate(); todSync(); return TOD.h; };
host.__ff = function (sec) { for (var t = 0; t < sec; t += 0.05) { now += 0.05; updateSim(0.05); } updatePanels(); return ST.done; };
host.__adv = function (sec) { for (var t = 0; t < sec; t += 0.05) { now += 0.05; step(0.05); } updatePanels(); return FLY ? FLY.t : -1; };
host.__st = function () { return { view: V.k, draws: DRAWS.length, fxa: FXA.n, bat: BAT.nv, bati: BAT.ni, gl2: GL2, done: ST.done, fly: FLYS.length, meet: MT ? MT.title + ":" + MT.ph : "-", n: living().length, modes: PEOPLE.map(function (p) { return p.n + ":" + p.mode + (p.task ? "/" + p.task : "") + (p.cur ? "*" + p.cur.st : "") + "q" + p.q.length + (p.gone ? "!gone" : ""); }).join(" "), proj: PROJS.map(function (P) { return P.id + ":" + P.state + ":" + P.done + "/" + P.N + ":" + Math.round(projProg(P) * 100); }).join(" ") }; };
host.__setcam = function (t, yaw, pitch, dist) { CV = { t: t, yaw: yaw, pitch: pitch, dist: dist }; DRG = { yaw: 0, pitch: 0, zoom: 1 }; FLY = null; view = 0; };
host.__geo = function () { function c(g) { return g.n + "v/" + (g.cnt || (g.I ? g.I.length : 0)) + "i"; } return { office: c(OFFICE), dept: DEPTM.map(c), meet: c(ROOMM.meet), nodes: NODES.length }; };
host.__q = function (o) { if (o.auto !== undefined) Q.auto = o.auto; if (o.sh !== undefined) Q.sh = o.sh; if (o.ms !== undefined) Q.ms = o.ms; if (o.dpr) { Q.dpr = o.dpr; layout(); } return Q; };
host.__E = function (k, v) { E[k] = v; return E[k]; };
host.__fc = function () { return FC; };
host.__proj = function (pts) { snapCam(); camUpdate(0); return { L: [L.rx0, L.rx1, L.ry0, L.ry1, L.w, L.h], p: pts.map(function (p) { var q = proj(p); return q ? [Math.round(q[0]), Math.round(q[1])] : null; }) }; };
host.__screen = function (n) { camUpdate(0); var p = byName(n); var q = p ? proj(headPt(p, 0)) : null; return q ? [Math.round(q[0]), Math.round(q[1])] : null; };
host.__task = function (name, tpl, n, urgent, chain) { return createTask(name, tpl, TPL[tpl].S || customStages(chain || [3, 6, -1]), n, !!urgent).id; };
host.__hr = function (op, a, b) { if (op === "add") return hrAdd(a); if (op === "edit") return hrEdit(byName(a), b); if (op === "del") return hrRemove(byName(a)); if (op === "reset") { hrReset(); return null; } };
host.__open = function (w) { if (w === "task") openTask(); else if (w === "hr") openHR(); else if (w === "form") { openHR(); openForm(null); } else closeModal(); return !!MODAL; };
host.__inv = function () {
  var errs = [], loc = {};
  function mark(it, w) { (loc[it.id] = loc[it.id] || []).push(w); }
  PEOPLE.forEach(function (p) {
    p.q.forEach(function (it) { mark(it, p.n + ".q"); if (it.who !== p) errs.push(it.id + " in " + p.n + ".q but who=" + (it.who && it.who.n)); if (it.st !== "q") errs.push(it.id + " in q st=" + it.st); });
    if (p.cur) mark(p.cur, p.n + ".cur"); if (p.carry) mark(p.carry, p.n + ".carry");
    if (p.inb < 0) errs.push(p.n + " inb<0");
  });
  FLYS.forEach(function (it) { mark(it, "fly"); });
  PROJS.forEach(function (P) { P.items.forEach(function (it) { var l = loc[it.id] || []; if (it.st === "back" || it.st === "fin") { if (l.length) errs.push(it.id + " " + it.st + " but at " + l.join(",")); } else if (l.length !== 1) errs.push(it.id + " st=" + it.st + " at [" + l.join(",") + "]"); }); });
  PEOPLE.forEach(function (p) { if (p.gone) return; var n = 0; FLYS.forEach(function (it) { if (it.who === p) n++; }); PEOPLE.forEach(function (q) { if (q.carry && q.carry.who === p) n++; }); if (n !== p.inb) errs.push(p.n + " inb " + p.inb + " vs " + n); });
  DEPTS.forEach(function (D) { D.seats.concat([D.headSeat]).forEach(function (s) { if (s.own && (s.own.gone || s.own.seat !== s)) errs.push("seat " + D.zh + " own " + s.own.n); }); });
  living().forEach(function (p) { if (p.seat.own !== p) errs.push(p.n + " seat not owned"); if (p.d >= 0 && p.head !== (DEPTS[p.d].headSeat.own === p)) errs.push(p.n + " head flag"); });
  PEOPLE.forEach(function (p) { if (!p.gone && p.mode !== "walk" && p.at && p.at !== p.home && p.task === null && p.wait <= 0) errs.push(p.n + " stray at " + p.at.x.toFixed(1) + "," + p.at.z.toFixed(1)); });
  return errs;
};
host.__cpu = function (n) { if (!BAT.v) batInit(); var t0 = performance.now(), i; for (i = 0; i < n; i++) { now += 0.016; step(0.016); buildScene(now, 0.016); placeHud(); } return { ms: (performance.now() - t0) / n, bat: BAT.nv, bati: BAT.ni, fxa: FXA.n, draws: DRAWS.length }; };


  rafId = requestAnimationFrame(frame);

  function destroy() {
    destroyed = true;
    if (rafId) cancelAnimationFrame(rafId);
    if (clockTimer) clearInterval(clockTimer);
    if (roInst) roInst.disconnect();
    window.removeEventListener("resize", onWinResize);
    document.removeEventListener("fullscreenchange", onFsChange);
    document.removeEventListener("keydown", onKeyDown);
    try {
      if (gl && gl.getExtension) {
        var lose = gl.getExtension("WEBGL_lose_context");
        if (lose) lose.loseContext();
      }
    } catch (e) {}
  }

  function syncBackendData(data) {
    if (!data) return;
    if (data.wsConnected !== undefined) {
      var connEl = SR.getElementById("connTxt");
      if (connEl) {
        connEl.textContent = data.wsConnected
          ? "云端引擎已连接 · 实时总线 LIVE · 关窗后台持续运行"
          : "云端引擎断开 · 正在尝试重连总线";
      }
    }
    if (typeof data.totalTokens === "number") {
      var tokEl = SR.getElementById("kTok");
      if (tokEl) tokEl.textContent = data.totalTokens.toLocaleString();
    }
    if (typeof data.pendingCount === "number") {
      var revEl = SR.getElementById("kRev");
      if (revEl) revEl.textContent = String(data.pendingCount);
    }
    if (data.projects && Array.isArray(data.projects)) {
      data.projects.forEach(function (rp) {
        var existing = PROJS.find(function (p) { return p.realBackendId === rp.id || p.id === rp.id; });
        if (!existing) {
          var P = createTask(rp.name || rp.id, 0, null, 3, false);
          P.realBackendId = rp.id;
          P.user = true;
        }
      });
      updatePanels();
    }
  }

  function pushBackendEvent(evt) {
    if (!evt) return;
    var logEl = SR.getElementById("log");
    if (logEl) {
      var item = document.createElement("div");
      item.className = "it" + (evt.type === "APPROVAL_REQUIRED" ? " rev" : evt.type === "CLOUD_PROJECT_CREATED" ? " hi" : "");
      var timeStr = new Date().toLocaleTimeString();
      var msg = evt.name || evt.message || evt.type;
      if (evt.type === "CLOUD_PROJECT_CREATED") msg = "云端立项成功: " + evt.name + " (" + (evt.realId || evt.projId) + ")";
      else if (evt.type === "STAGE_CHANGED") msg = "工序推进: " + (evt.stage_name || evt.stage_key);
      else if (evt.type === "APPROVAL_REQUIRED") msg = "待办审批令: " + (evt.title || "请签章");
      item.innerHTML = '<span class="tm">' + timeStr + '</span><span class="dot"></span><span class="txt">' + msg + '</span>';
      logEl.insertBefore(item, logEl.firstChild);
      while (logEl.children.length > 30) logEl.removeChild(logEl.lastChild);
    }

    var chief = PEOPLE.find(function (p) { return p.head === -1 || p.d === -1; });
    if (chief && evt.type === "CLOUD_PROJECT_CREATED") {
      say(chief, "已同步云端流水线，关窗也会持续跑！", 5);
    } else if (chief && evt.type === "APPROVAL_REQUIRED") {
      say(chief, "主公，有待办审批令已推送至控制台！", 6);
    }
  }

  return {
    destroy: destroy,
    syncBackendData: syncBackendData,
    pushBackendEvent: pushBackendEvent,
    openTaskModal: openTask,
    goView: host.__go,
  };

}
