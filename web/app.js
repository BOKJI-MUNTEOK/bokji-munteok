"use strict";

const main = document.querySelector("main");
const dialog = document.querySelector("dialog");
const state = { data: null, answers: {}, step: 0, mode: "form", quick: "", result: null,
  filters: { query: "", source: "전체", band: "전체", sort: "easy", online: false }, weights: {}, busy: false };
const titles = { "/": "홈", "/find": "내 복지 찾기", "/all": "전체 서비스", "/analysis": "데이터 분석", "/guide": "안내" };
const questionTitles = ["살고 계신 지역은 어디세요?", "나이가 어떻게 되세요?", "직업은 무엇인가요?", "함께 사는 가족은 어떻게 되세요?", "해당되는 것이 있나요?", "요즘 가장 도움이 필요한 건 뭔가요?"];
const icons = ["🎓", "🌿", "🌱", "🧸", "♿", "🏡"];
const colors = ["#256ef4", "#63a6fa", "#64b59a", "#e6a04c", "#ae8bcd", "#8294a8"];
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const safeUrl = value => { try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) ? esc(url.href) : "#"; } catch { return "#"; } };
const band = n => n < 40 ? ["신청 쉬움", "low"] : n < 60 ? ["신청 보통", "mid"] : ["신청 까다로움", "high"];
const badge = n => `<span class="badge ${band(n)[1]}">${band(n)[0]}</span>`;
const byId = id => state.data.rows.find(r => r.svc.servId === id);
const money = text => (text || "").match(/(?:(?:1인당|가구당|학기당|연간|연|매월|월|최대)\s*)+[\d,.]+\s*(?:만\s*|천\s*|억\s*)?원(?:\/월|까지)?/)?.[0] || "";

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === "string" ? error.detail : "요청하지 못했어요. 잠시 후 다시 시도해 주세요.");
  }
  return response;
}

function heading(title, note = "") {
  return `<div class="crumb">홈 › ${esc(titles[location.pathname] || title)}</div><h1 class="page-title">${esc(title)}</h1>${note ? `<p class="notice">${note}</p>` : ""}`;
}

function quickButtons(panel = false) {
  return `<div class="${panel ? "panel quick-panel" : "quick-grid"}">${panel ? '<h3>생애주기·대상별 복지</h3><small>질문 없이 바로 보기</small>' : ""}${state.data.quick.map((q, i) => `<button data-action="quick" data-value="${esc(q)}"><span aria-hidden="true">${icons[i]}</span> ${esc(q)}</button>`).join("")}</div>`;
}

function card(r, why = []) {
  const s = r.svc, what = r.ai?.easy.what || s.summary;
  return `<article class="card">${badge(r.score)}<h3>${esc(s.servNm)}</h3><div class="money">${esc(money(what)) || "&nbsp;"}</div><p class="what">${esc(what)}</p><small>${esc(why.length ? why.join(" · ") : s.region)}</small><button data-action="detail" data-id="${esc(s.servId)}" aria-label="${esc(s.servNm)} 신청 방법 보기">신청 방법 보기 →</button></article>`;
}

function cards(items) {
  return `<div class="cards">${items.map(item => card(item.id ? byId(item.id) : item, item.why || [])).join("")}</div>`;
}

function home() {
  const demoName = new URLSearchParams(location.search).get("service");
  const demo = state.data.rows.find(r => r.svc.servNm === demoName || r.svc.servId === demoName);
  return `<section><div class="hero"><div class="eyebrow">복지 서비스 찾기</div><h1>내가 받을 수 있는 복지,<br>질문 몇 개면 알 수 있어요</h1><p class="lead">지역과 나이, 가족 상황을 고르면 복지 ${state.data.rows.length}개 중<br>조건이 맞는 것만 골라 드려요.</p><div class="actions"><button class="primary" data-action="start">내 복지 찾기 →</button><a href="/all" data-nav>전체 서비스 보기 ›</a></div><small>질문 6개 · 시연용 샘플 ${state.data.rows.length}개 서비스로 동작 · 입력한 내용은 저장하지 않아요</small></div>${demo ? `<h2>시연 서비스</h2>${cards([demo])}` : ""}<h2>누구를 위한 복지를 찾으세요?</h2>${quickButtons()}<h2>이렇게 진행돼요</h2><div class="steps-grid">${[["질문에 답하기", "지역·나이 등 6가지를 골라요"], ["맞춤 복지 확인", "조건이 맞고 신청이 쉬운 순서로"], ["서류 준비", "신청서 작성 도우미까지 한 번에"]].map(([t,d],i) => `<div><b><span class="step-num">${i+1}</span>${t}</b><p>${d}</p></div>`).join("")}</div></section>`;
}

function wizardField() {
  const [key, , options] = state.data.questions[state.step], value = state.answers[key] || "";
  if (key === "place") {
    const sido = [...new Set(state.data.places.map(p => p[0]))].find(p => value.includes(p)) || "";
    return `<label class="field"><span>시·도</span><select name="sido"><option value="">선택 안 함</option>${[...new Set(state.data.places.map(p => p[0]))].map(p => `<option ${sido === p ? "selected" : ""}>${esc(p)}</option>`).join("")}</select></label><label class="field"><span>시·군·구</span><select name="sgg" ${sido ? "" : "disabled"}><option value="">전체</option>${state.data.places.filter(p => p[0] === sido && p[1]).map(p => `<option ${value.includes(p[1]) ? "selected" : ""}>${esc(p[1])}</option>`).join("")}</select></label><small>시연 샘플에 있는 지역만 나와요. 고르지 않으면 전국 복지만 찾아요.</small>`;
  }
  if (key === "age") return `<label class="field"><span>나이</span><input name="answer" type="number" min="0" max="120" step="1" required placeholder="예: 32" value="${esc(value.match(/\d+/)?.[0] || "")}" inputmode="numeric"></label>`;
  const selected = value.split(", "), other = selected.filter(v => v && v !== "없음" && !options.includes(v)).join(", ");
  return `<div class="choices">${options.filter(q => q !== "기타(직접 입력)").map(q => `<label class="choice"><input type="${key === "job" ? "radio" : "checkbox"}" name="choice" value="${esc(q)}" ${selected.includes(q) ? "checked" : ""}>${esc(q)}</label>`).join("")}</div><label class="field"><span>기타 (직접 입력, 선택)</span><input name="other" maxlength="200" placeholder="자유롭게 적어 주세요" value="${esc(other)}"></label><small>${key === "job" ? "가장 가까운 것 하나를 골라 주세요." : "여러 개 골라도 돼요. 없으면 그냥 넘어가세요."}</small>`;
}

function wizard() {
  return `<div class="progress-label">${state.step + 1} / 6</div><progress value="${state.step+1}" max="6" aria-label="질문 진행"></progress><form id="wizard"><div class="panel question"><h2>${questionTitles[state.step]}</h2>${wizardField()}<div id="form-error" role="alert"></div></div><div class="wizard-actions">${state.step ? '<button type="button" data-action="previous">← 이전</button>' : "<span></span>"}<button type="submit" class="primary">${state.step === 5 ? "결과 보기" : "다음 →"}</button></div></form>`;
}

function chat() {
  const messages = [];
  for (let i = 0; i <= Math.min(state.step, 5); i++) {
    const [key, question] = state.data.questions[i];
    messages.push(`<div class="bubble">${esc(question.replace(/\*\*/g, ""))}</div>`);
    if (i < state.step) messages.push(`<div class="bubble user">${esc(state.answers[key])}</div>`);
  }
  const options = state.step === 0 ? state.data.places.map(p => p[1]).filter(Boolean).slice(0, 6) : state.data.questions[state.step][2].filter(q => q !== "기타(직접 입력)");
  return `<div class="progress-label">질문 ${state.step+1} / 6 · 키워드로 답을 정리해요</div><div class="chat" aria-label="상담 대화">${messages.join("")}</div><div class="choices">${options.map(q => `<button data-action="chat-choice" data-value="${esc(q)}">${esc(q)}</button>`).join("")}</div><form id="chat-form" class="chat-input"><label class="sr-only" for="chat-answer" hidden>상담 답변</label><input id="chat-answer" name="answer" maxlength="300" aria-label="상담 답변" placeholder="답을 적어 주세요" required><button class="primary">보내기</button></form><div id="form-error" role="alert"></div><button class="link-button" data-action="reset">처음부터</button>`;
}

function result() {
  const recs = state.result.results;
  let hit = recs.filter(r => r.why.length), loose = recs.filter(r => !r.why.length);
  if (!hit.length) { hit = loose; loose = []; }
  const rows = hit.map(r => byId(r.id));
  const stats = [["찾은 복지", hit.length], ["신청이 쉬운 것", rows.filter(r => r.score < 40).length], ["온라인 신청 가능", rows.filter(r => r.ext.online).length], ["우리 동네 복지", rows.filter(r => r.svc.src === "지자체").length]].filter(([,n],i) => i === 0 || n);
  return `<div class="actions"><h2>${esc(state.quick ? state.quick + " 대상" : "맞춤")} 복지 ${hit.length}개</h2><button data-action="reset">다시 고르기</button></div><p class="muted">${esc(state.result.summary.join(" · "))}</p><div class="metrics">${stats.map(([l,n]) => `<div class="metric"><small>${l}</small><b>${n}개</b></div>`).join("")}</div>${!recs.length ? '<div class="info">조건에 맞는 복지를 샘플에서 찾지 못했어요. 다시 고르기에서 조건을 바꿔 보세요.</div>' : cards(hit.slice(0,4))}${hit.length > 4 ? `<details><summary>나머지 ${hit.length-4}개 더 보기</summary>${cards(hit.slice(4))}</details>` : ""}${loose.length ? `<details><summary>대상 구분 없이 누구나 볼 수 있는 복지 ${loose.length}개</summary>${cards(loose)}</details>` : ""}<p class="notice">공식 목록의 지역·생애주기·대상 태그로 고른 결과예요. 실제 자격은 담당 기관에서 확인해요.</p>`;
}

function find() {
  return `<section>${heading(state.step >= 6 ? "내가 받을 수 있는 복지" : "질문 6개로 받을 수 있는 복지 찾기", `ⓘ 시연용 샘플 <b>${state.data.rows.length}개 서비스</b> 안에서 찾아요`)}<div class="find-layout"><div><div class="mode" aria-label="찾는 방법"><button data-action="mode" data-value="form" aria-pressed="${state.mode === "form"}">☑ 하나씩 골라서 찾기</button><button data-action="mode" data-value="chat" aria-pressed="${state.mode === "chat"}">☏ 대화로 찾기</button></div>${state.step >= 6 && state.result ? result() : state.mode === "chat" ? chat() : wizard()}</div>${quickButtons(true)}</div></section>`;
}

function browseResults() {
  const f = state.filters;
  const rows = state.data.rows.filter(r => (f.source === "전체" || r.svc.src === f.source) && (f.band === "전체" || band(r.score)[1] === f.band) && (!f.online || r.ext.online) && (!f.query || [r.svc.servNm, r.svc.org, r.svc.region, r.svc.summary].join(" ").includes(f.query.trim()))).sort((a,b) => f.sort === "name" ? a.svc.servNm.localeCompare(b.svc.servNm, "ko") : f.sort === "hard" ? b.score-a.score : a.score-b.score);
  return `<p class="muted" role="status">${rows.length}건</p>${rows.length ? cards(rows) : '<p class="empty">조건에 맞는 서비스가 없어요. 검색어나 필터를 바꿔 보세요.</p>'}`;
}

function browse() {
  const f = state.filters;
  const select = (key, label, values) => `<label>${label}<select data-filter="${key}">${values.map(([v,l])=>`<option value="${v}" ${f[key] === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>`;
  return `<section>${heading("전체 서비스")}<div class="panel filters"><label class="search">서비스 검색<input data-filter="query" type="search" placeholder="서비스 이름, 기관, 지역으로 찾기 (예: 청년, 장애, 해남)" value="${esc(f.query)}"></label>${select("source","구분", [["전체","중앙·지자체 전체"],["중앙","중앙"],["지자체","지자체"]])}${select("band","신청 난이도", [["전체","전체"],["low","쉬움"],["mid","보통"],["high","까다로움"]])}${select("sort","정렬", [["easy","신청 쉬운 순"],["hard","신청 어려운 순"],["name","이름순"]])}<label class="check"><input type="checkbox" data-filter="online" ${f.online ? "checked" : ""}>온라인 신청 가능만</label></div><div id="services">${browseResults()}</div></section>`;
}

function weightsForm() {
  return `<details><summary>가중치 조절 (팀 결정 전 안)</summary><form id="weights-form"><div class="weights">${Object.entries(state.data.weight_labels).map(([k,l]) => `<label>${l} <output>${state.weights[k]}</output><input type="range" name="${k}" min="0" max="5" step="0.5" value="${state.weights[k]}"></label>`).join("")}</div><div class="actions"><button class="primary">가중치 적용</button><button type="button" data-action="default-weights">기본값으로</button></div></form></details>`;
}

function analysisPage() {
  return `<section>${heading("데이터 분석", "문턱 점수는 AI를 쓰지 않고 정해 둔 기준표로 계산합니다.")}${weightsForm()}<div id="analysis-results" aria-live="polite"><p class="loading">분석 결과를 불러오고 있어요…</p></div></section>`;
}

async function loadAnalysis() {
  const weights = {...state.weights};
  const a = await (await api("/api/analysis", {weights})).json();
  const target = document.querySelector("#analysis-results");
  if (!target || JSON.stringify(weights) !== JSON.stringify(state.weights)) return;
  const rows = a.rows, average = Math.round(rows.reduce((n,r) => n+r.score,0)/rows.length);
  const parts = Object.keys(rows[0].parts);
  const bars = (items, scale=100) => items.map(([name,n]) => `<div class="bar-row"><span>${esc(name)}</span><div class="bar-track"><i style="width:${Math.min(100,n/scale*100)}%"></i></div><b>${n}</b></div>`).join("");
  target.innerHTML = `<div class="metrics">${[["분석한 복지서비스", `${rows.length}건`],["평균 문턱 점수", `${average}점`],["온라인 신청 불가", `${Math.round(rows.filter(r=>!r.online).length/rows.length*100)}%`],["쉬운 설명 숫자 검사 통과",`${a.number_ok}/${a.ai_count}`]].map(([l,n])=>`<div class="metric"><small>${l}</small><b>${n}</b></div>`).join("")}</div><div class="two-col"><div class="panel"><h3>문턱 점수 분포</h3>${bars([["신청 쉬움 · 40점 미만",rows.filter(r=>r.score<40).length],["신청 보통 · 40~59점",rows.filter(r=>r.score>=40&&r.score<60).length],["신청 까다로움 · 60점 이상",rows.filter(r=>r.score>=60).length]], rows.length)}</div><div class="panel"><h3>무엇이 문턱을 높이나</h3><small>전체 서비스 항목별 평균 · 100점 환산</small>${bars(parts.map(k=>[k, Math.round(rows.reduce((n,r)=>n+r.parts[k],0)/rows.length*10)/10]))}</div></div><div class="panel"><h3>문턱이 높은 서비스 15개</h3><div class="legend">${parts.map((k,i)=>`<span><i style="background:${colors[i]}"></i>${k}</span>`).join("")}</div>${rows.slice(0,15).map(r=>`<div class="bar-row"><span>${esc(r.name)}</span><div class="bar-track">${parts.map((k,i)=>`<i style="width:${r.parts[k]}%;background:${colors[i]}" title="${esc(k)} ${r.parts[k]}점"></i>`).join("")}</div><b>${r.score}점</b></div>`).join("")}</div><details><summary>전체 점수 상세 ${rows.length}건</summary><div class="table-wrap"><table><thead><tr><th>서비스</th><th>점수</th><th>구분</th><th>이유</th></tr></thead><tbody>${rows.map(r=>`<tr><td><button class="link-button" data-action="detail" data-id="${r.id}">${esc(r.name)}</button></td><td>${r.score}</td><td>${r.src}</td><td>${esc(r.reason)}</td></tr>`).join("")}</tbody></table></div></details><h2>정답표와 비교</h2><p class="notice">정답표 ${a.gold_count}건 · AI 초안을 사람이 검수 ${a.gold_mix["사람 검수"]}건 · AI가 작성·재검토 ${a.gold_mix["AI 재검토"]}건</p><div class="table-wrap"><table><thead><tr><th>항목</th><th>규칙 ↔ 정답표</th><th>AI ↔ 정답표</th><th>규칙 ↔ AI</th></tr></thead><tbody>${Object.entries(a.labels).map(([k,l])=>`<tr><td>${esc(l)}</td><td>${a.rule_gold[k] ?? "—"}%</td><td>${a.ai_gold[k] ?? "—"}%</td><td>${a.rule_ai[k] ?? "—"}%</td></tr>`).join("")}</tbody></table></div>`;
}

function guide() {
  return `<section>${heading("안내")}<div class="two-col"><div class="panel"><h2>문턱 점수 계산 기준</h2><p>항목 점수를 더한 뒤 0~100으로 바꿉니다. AI를 쓰지 않습니다.</p><table><thead><tr><th>항목</th><th>기본 가중치</th><th>상한</th></tr></thead><tbody>${Object.entries(state.data.weight_labels).map(([k,l])=>`<tr><td>${l}</td><td>${state.data.weights[k]}</td><td>${state.data.caps[k] ?? "—"}</td></tr>`).join("")}</tbody></table><p class="notice">40점 미만 신청 쉬움 · 40~59점 보통 · 60점 이상 까다로움</p></div><div class="panel"><h2>내 복지 찾기</h2><p>지역·생애주기·대상·관심 주제 태그로 서비스를 골라요. 자격을 판정하지 않으며, 실제 자격은 담당 기관에서 확인합니다.</p><h3>쉬운 설명 검사</h3><p>저장된 AI 설명에서 원문의 나이·금액·기간 등 숫자가 빠지면 ‘사람 확인 필요’를 표시해요.</p><h3>신청 준비</h3><p>신청서·위임장 작성 도우미와 서류 발급처, 전화 상담 문구를 제공합니다. 접수를 대신하지 않아요.</p><h3>입력 내용</h3><p>입력은 현재 화면과 요청 처리 중에만 사용하며 서버 파일이나 데이터베이스에 저장하지 않아요. 새로고침하면 초기화됩니다.</p></div></div><small>공공데이터포털 중앙부처 15090532 · 지자체 15108347 · 샘플 ${state.data.rows.length}건 · 2026-11-10 수집</small></section>`;
}

function detailContent(r, tab) {
  const s = r.svc, easy = r.ai?.easy;
  if (tab === "easy") {
    if (!easy) return '<p class="info">아직 쉬운 설명이 없습니다. 원문에서 신청 안내를 확인해 주세요.</p>';
    return `<div class="easy-grid">${[["누가",easy.who],["무엇을",easy.what],["어떻게 신청",(r.ext.online ? "온라인 신청 가능" : "방문 신청") + "\n" + (easy.steps[0] || "")]].map(([t,c])=>`<div class="easy-box"><h3>${t}</h3><div class="pre">${esc(c)}</div></div>`).join("")}</div><h3>조건</h3><ul class="detail-list">${easy.conditions.map(c=>`<li>${esc(c)}</li>`).join("")}</ul><h3>할 일</h3><ol class="detail-list">${easy.steps.map(c=>`<li>${esc(c)}</li>`).join("")}</ol>${r.number_check?.ok ? `<p class="notice">✓ 원문 숫자 ${r.number_check.total}개가 모두 들어 있는지 확인했어요.</p>` : `<p class="warning">사람 확인 필요 — 빠진 숫자: ${esc(r.number_check?.missing.join(", "))}</p>`}`;
  }
  if (tab === "score") return `<h3>문턱 ${r.score}점</h3><p>${esc(r.reason)}</p>${Object.entries(r.parts).map(([k,v])=>`<div class="bar-row"><span>${k}</span><div class="bar-track"><i style="width:${r.raw ? v/r.raw*r.score : 0}%"></i></div><b>${v}</b></div>`).join("")}<p>조건 ${r.ext.conditions.length}개 · ${esc(r.ext.conditions.map(c=>c.type).join(", "))}</p><p>거치는 기관: ${esc(r.ext.agencies.join(" → ")) || "없음"}</p><p>안내문 난이도 ${r.read.points}점 · 문장 평균 ${r.read.avg_len}자</p><small>기준표 계산 · 항목 숫자는 원점수 · 막대는 100점 환산 · AI를 쓰지 않음</small>`;
  if (tab === "original") return [["target","지원대상"],["criteria","선정기준"],["benefit","지원내용"],["apply","신청방법"]].map(([k,l])=>`<h3>${l}</h3><p class="pre">${esc(s[k] || "(비어 있음)")}</p>`).join("");
  return `${!r.documents_listed ? '<p class="info">안내문에 서류 목록이 없어요. 기본 서류를 먼저 표시합니다. 정확한 목록은 첨부 공고문이나 담당 기관에서 확인하세요.</p>' : ""}<h3>${r.documents_listed ? "이 복지에 필요한 서류" : "기본으로 챙길 서류"}</h3>${r.documents.map(d=>`<div class="doc-row"><label class="check"><input type="checkbox">${esc(d.name)}</label><small>${d.where ? esc(d.where[0]) + (d.where[1] ? " · 온라인 발급 가능" : "") : "담당 기관에 물어보세요"}</small></div>`).join("")}<h3 style="margin-top:24px">기관 첨부 파일</h3>${(s.form_files || []).map(f=>`<div class="attachment"><span>${esc(f.name)}</span><a href="${safeUrl(f.url)}" target="_blank" rel="noopener noreferrer">원본 받기 ↗</a></div>`).join("") || '<p class="muted">등록된 첨부 파일이 없습니다.</p>'}<details><summary>방문이 어렵다면 이렇게 전화해 보세요</summary><p class="pre">${esc(r.call_script)}</p></details><p>문의: <b>${esc(s.contact || "담당 기관에 확인")}</b></p>${s.link ? `<a class="button" href="${safeUrl(s.link)}" target="_blank" rel="noopener noreferrer">복지로에서 신청 안내 보기 ↗</a>` : ""}<div class="panel" style="margin-top:24px"><h3>신청 준비 꾸러미 한 번에 받기</h3><p class="notice">신청 준비표, 미리 채운 신청서·위임장 작성 도우미(PDF), 기관 양식을 ZIP으로 드려요. 주민등록번호·서명·계좌는 비워 둡니다. 입력은 서버에 저장하지 않아요.</p>${state.result ? `<small>채울 정보: ${esc(state.result.summary.join(" · "))}</small>` : '<small>내 복지 찾기에서 질문에 답하면 지역·나이 등을 함께 채워요.</small>'}<form id="packet-form" data-id="${s.servId}"><div class="packet-fields">${[["name","이름",10],["phone","연락처",40],["address","상세 주소",300],["proxy","대신 내는 사람 성함",40],["relation","신청인과 관계",40]].map(([k,l,n])=>`<label>${l} (선택)<input name="${k}" maxlength="${n}" autocomplete="off"></label>`).join("")}</div><button class="primary" type="submit">꾸러미 만들고 ZIP 받기</button><div id="packet-status" role="status"></div></form></div>`;
}

function showDetail(id) {
  const r = byId(id);
  if (!r) return;
  dialog.dataset.id = id;
  dialog.innerHTML = `<div class="dialog-head"><div><h2 id="detail-title">${esc(r.svc.servNm)}</h2><small>${esc(r.svc.org)} · ${esc(r.svc.region)}</small> ${badge(r.score)}</div><button data-action="close" aria-label="신청 안내 닫기">✕</button></div><div class="tabs" role="tablist">${[["easy","쉬운 설명"],["docs","서류 준비"],["score","왜 이 점수?"],["original","원문"]].map(([k,l])=>`<button role="tab" id="tab-${k}" aria-controls="detail-body" data-action="tab" data-value="${k}" aria-selected="${k === "easy"}">${l}</button>`).join("")}</div><div id="detail-body" role="tabpanel" aria-labelledby="tab-easy">${detailContent(r,"easy")}</div>`;
  if (!dialog.open) dialog.showModal();
}

function render() {
  if (!state.data) return;
  const page = location.pathname;
  document.title = `${titles[page] || "페이지 없음"} · 복지문턱`;
  document.querySelectorAll("nav a").forEach(a => a.getAttribute("href") === page ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
  main.innerHTML = page === "/" ? home() : page === "/find" ? find() : page === "/all" ? browse() : page === "/analysis" ? analysisPage() : page === "/guide" ? guide() : '<h1>페이지를 찾을 수 없어요</h1><a href="/" data-nav>홈으로 돌아가기</a>';
  const chatbox = main.querySelector(".chat");
  if (chatbox) chatbox.scrollTop = chatbox.scrollHeight;
  if (page === "/analysis") loadAnalysis().catch(showError);
}

function navigate(path) {
  if (dialog.open) dialog.close();
  history.pushState({}, "", path);
  render();
  window.scrollTo(0, 0);
  main.focus({preventScroll:true});
}

function reset() {
  state.answers = {}; state.step = 0; state.quick = ""; state.result = null;
}

function showError(error) {
  const target = document.querySelector("#form-error") || document.querySelector("#analysis-results");
  if (target) target.innerHTML = `<p class="error" role="alert">${esc(error.message)}</p>`;
  else { const p = document.createElement("p"); p.className="error"; p.setAttribute("role","alert"); p.textContent=error.message; main.prepend(p); }
}

async function submitAnswer(value) {
  const key = state.data.questions[state.step][0];
  const answers = {...state.answers, [key]: value};
  const result = await (await api("/api/recommend", {answers})).json();
  state.answers = answers; state.result = result; state.quick = ""; state.step++;
  render();
}

function readWizard(form) {
  const data = new FormData(form), key = state.data.questions[state.step][0];
  if (key === "place") return [data.get("sido"), data.get("sgg")].filter(Boolean).join(" ") || "없음";
  if (key === "age") return `${data.get("answer")}세`;
  const other = String(data.get("other") || "").trim();
  // 직업은 하나만 선택한다. 직접 입력을 우선한다.
  if (key === "job" && other) return other;
  return [...data.getAll("choice"), other].filter(Boolean).join(", ") || "없음";
}

// 비동기 요청 중에는 현재 화면의 중복 제출·질문 전환을 막는다.
async function run(action, form) {
  if (state.busy) return;
  state.busy = true;
  const controls = [...document.querySelectorAll("button,input,select")].filter(e=>!e.disabled);
  controls.forEach(e=>e.disabled=true);
  try { await action(); }
  catch(error) {
    if (form?.id === "packet-form") document.querySelector("#packet-status").innerHTML = `<p class="error">${esc(error.message)}</p>`;
    else showError(error);
  }
  finally { controls.forEach(e=>e.disabled=false); state.busy=false; }
}

document.addEventListener("click", event => {
  const nav = event.target.closest("a[data-nav]");
  if (nav && !event.ctrlKey && !event.metaKey && !event.shiftKey && event.button === 0) {
    event.preventDefault(); if (!state.busy) navigate(nav.getAttribute("href")); return;
  }
  const button = event.target.closest("button[data-action]");
  if (!button || button.disabled || state.busy) return;
  const {action,value,id} = button.dataset;
  if (action === "close") dialog.close();
  if (action === "start") { reset(); state.mode="form"; navigate("/find"); }
  if (action === "reset") { reset(); render(); }
  if (action === "previous") { state.answers[state.data.questions[state.step][0]] = readWizard(document.querySelector("#wizard")); state.step--; render(); }
  if (action === "mode") { state.mode=value; render(); }
  if (action === "detail") showDetail(id);
  if (action === "quick") run(async () => {
    const result = await (await api("/api/recommend", {quick:value})).json();
    reset(); state.quick=value; state.result=result; state.step=6; state.mode="form"; navigate("/find");
  });
  if (action === "chat-choice") run(()=>submitAnswer(value));
  if (action === "default-weights") { state.weights={...state.data.weights}; render(); }
  if (action === "tab") {
    dialog.querySelectorAll('[role="tab"]').forEach(t=>t.setAttribute("aria-selected", String(t===button)));
    const body=document.querySelector("#detail-body"); body.setAttribute("aria-labelledby",button.id);
    body.innerHTML=detailContent(byId(dialog.dataset.id),value);
  }
});

document.addEventListener("submit", event => {
  const form=event.target;
  if (!["wizard","chat-form","weights-form","packet-form"].includes(form.id)) return;
  event.preventDefault();
  // FormData는 disabled 처리 전에 읽는다.
  if (form.id === "wizard") { const value=readWizard(form); run(()=>submitAnswer(value),form); }
  if (form.id === "chat-form") { const value=new FormData(form).get("answer"); run(()=>submitAnswer(value),form); }
  if (form.id === "weights-form") { state.weights=Object.fromEntries([...new FormData(form)].map(([k,v])=>[k,Number(v)])); run(loadAnalysis,form); }
  if (form.id === "packet-form") {
    const inputs=Object.fromEntries(new FormData(form));
    run(async()=>{
      // 폼 요소가 잠긴 뒤에도 값이 전송되도록 미리 읽은 입력을 전달한다.
      const status=document.querySelector("#packet-status");
      status.innerHTML='<p class="info">작성 도우미와 기관 양식을 준비하고 있어요…</p>';
      const response=await api("/api/packet",{service_id:form.dataset.id,answers:state.answers,quick:state.quick,extra:inputs});
      const url=URL.createObjectURL(await response.blob()), link=document.createElement("a");
      link.href=url;link.download=`신청준비_${byId(form.dataset.id).svc.servNm.slice(0,30)}.zip`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
      const n=Number(response.headers.get("X-Missing-Files")||0);
      status.innerHTML=`<p class="${n ? "warning" : "info"}">${n ? `원본 ${n}개는 받지 못해 ZIP에 다운로드 주소를 넣었어요. 위 기관 첨부 파일에서도 받을 수 있어요.` : "꾸러미가 준비됐어요. 내려받은 ZIP 파일을 확인해 주세요."}</p>`;
    },form);
  }
});

document.addEventListener("input",event=>{
  const field=event.target;
  if (field.dataset.filter) {
    state.filters[field.dataset.filter]=field.type === "checkbox" ? field.checked : field.value;
    document.querySelector("#services").innerHTML=browseResults();
  }
  if (field.type === "range") field.parentElement.querySelector("output").textContent=field.value;
});
document.addEventListener("change",event=>{
  if(event.target.name === "sido") {
    const select=document.querySelector('select[name="sgg"]'),sido=event.target.value;
    select.disabled=!sido;
    select.innerHTML='<option value="">전체</option>'+state.data.places.filter(p=>p[0]===sido&&p[1]).map(p=>`<option>${esc(p[1])}</option>`).join("");
  }
});
window.addEventListener("popstate",()=>{ if(dialog.open)dialog.close();render(); });

async function init() {
  try {
    state.data=await(await api("/api/bootstrap")).json();
    state.weights={...state.data.weights};
    const service=new URLSearchParams(location.search).get("service");
    if(location.pathname === "/all" && service) state.filters.query=service;
    render();
  } catch(error) {
    main.innerHTML=`<h1>복지 정보를 불러오지 못했어요</h1><p class="error">${esc(error.message)}</p><button id="retry">다시 불러오기</button>`;
    document.querySelector("#retry").addEventListener("click",init);
  }
}
init();
