"""복지문턱 시연 화면. 실행: .venv/bin/python -m streamlit run app.py"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import altair as alt
import pandas as pd
import streamlit as st

import evaluate
import intake
import llm
import match
import numcheck
import packet
import paperwork
import rules
import score
from parse import load_all
from pipeline import build

BRAND = "복지문턱"
LOGO = Path(__file__).resolve().parent / "assets" / "logo"
st.set_page_config(page_title=BRAND, page_icon="🛤️", layout="wide")
st.markdown("""
<style>
.gov-top{background:#F4F5F6;border-bottom:1px solid #DDE1E6;margin:0 -100vw 14px;padding:7px 100vw;font-size:.85rem;color:#464C53}
.gov-top b{color:#063A74}
[data-testid=stMainBlockContainer]{padding-top:2.2rem}
[data-testid=stMain]{overflow-x:hidden}
[data-testid=InputInstructions]{display:none}  /* 'Press Enter to apply' 영어 안내: 칸 밖을 누르거나 다음을 눌러도 적용된다 */
/* 화면 전환: 새로 그려지는 내용만 살짝 떠오르며 나타나고, 다시 그리는 동안 흐려지지 않게 */
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
[data-testid=stMain] [data-testid=stVerticalBlock]>*{animation:rise .32s cubic-bezier(.2,.7,.3,1) both}
[data-testid=stMain] [data-stale=true]{opacity:1 !important;transition:none !important}
[data-testid=stSidebarNav] a{transition:background-color .15s ease}
@media (prefers-reduced-motion:reduce){[data-testid=stMain] [data-testid=stVerticalBlock]>*{animation:none}}
.st-key-home_hero{padding:28px 4px 8px}
.home-tag{display:inline-block;font-size:.85rem;font-weight:600;color:#256EF4;margin-bottom:12px}
.home-t{font-size:2.3rem;font-weight:700;color:#191F28;line-height:1.38;letter-spacing:-.03em}
.home-s{font-size:1.06rem;font-weight:500;color:#4E5968;margin:14px 0 22px;letter-spacing:-.01em}
.home-meta{font-size:.85rem;color:#8B95A1;margin-top:2px}
.st-key-home_start button{min-height:44px;padding:0 30px;border-radius:999px;font-size:.98rem;font-weight:600}
.st-key-home_all button{font-size:1rem;color:#256EF4}
.sec-t{font-size:1.2rem;font-weight:700;color:#191F28;letter-spacing:-.02em;margin:34px 0 10px}
.st-key-home_quick button{min-height:52px;padding:8px 10px;border:none;border-radius:12px;background:#F2F4F6;color:#333D4B;font-size:.95rem;font-weight:600}
.st-key-home_quick button>div{flex-direction:column;gap:6px}
.st-key-home_quick button [data-testid=stIconMaterial]{font-size:1.2rem;color:#256EF4}
.st-key-home_quick button:hover{background:#E8F3FF;color:#191F28}
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:28px;border-top:1px solid #E5E8EB;padding-top:18px}
.steps .n{display:inline-block;width:24px;height:24px;border-radius:50%;background:#191F28;color:#fff;font-size:.8rem;font-weight:700;text-align:center;line-height:24px;margin-right:8px}
.steps b{font-size:1.02rem;color:#191F28}
.steps p{font-size:.92rem;color:#6B7684;margin:6px 0 0 32px}
@media (max-width:640px){.steps{grid-template-columns:1fr}.home-t{font-size:1.8rem}}
.st-key-chatbox{background:#F7F8FA}
.st-key-chatbox [data-testid=stChatMessage]{background:transparent;padding:2px 0;gap:10px;align-items:flex-start}
.st-key-chatbox [data-testid=stChatMessageContent]{flex:0 1 auto;width:fit-content;max-width:80%;margin:0 !important;padding:10px 14px;line-height:1.6;
  background:#fff;border:1px solid #DDE1E6;border-radius:4px 16px 16px 16px}
.st-key-chatbox [data-testid=stChatMessage]:has([data-testid=stChatMessageAvatarUser]){flex-direction:row-reverse}
.st-key-chatbox [data-testid=stChatMessage]:has([data-testid=stChatMessageAvatarUser]) [data-testid=stChatMessageContent]{
  background:#256EF4;border-color:#256EF4;border-radius:16px 4px 16px 16px;color:#fff}
.st-key-chatbox [data-testid=stChatMessage]:has([data-testid=stChatMessageAvatarUser]) [data-testid=stChatMessageContent] *{color:#fff}
.st-key-chatbox [data-testid=stChatMessageAvatarUser]{background:#E8F3FF;color:#256EF4}
.st-key-chatbox [data-testid=stChatMessageAvatarUser] *{color:#256EF4}
.st-key-chatbox [data-testid=stChatMessageContent] p{margin-bottom:.4em}
.st-key-chatbox [data-testid=stChatMessageContent] p:last-child{margin-bottom:0}
.st-key-chatbox [data-testid=stChatMessageContent] [data-testid=stMarkdownContainer]{margin:0}
.crumb{font-size:.82rem;color:#6D7882;margin-bottom:4px}
.page-t{font-size:1.75rem;font-weight:700;color:#1E2124;letter-spacing:-.5px;padding-bottom:12px;border-bottom:2px solid #063A74;margin-bottom:6px}
.notice{font-size:.88rem;color:#464C53;margin:2px 0 6px}
.notice b{color:#063A74}
.q{font-size:1.4rem;font-weight:700;color:#063A74;margin:2px 0 10px;line-height:1.4}
.st-key-mode button{min-height:46px;font-size:1rem;font-weight:600}
.st-key-mode button[aria-checked=true]{background:#256EF4 !important;color:#fff !important;border-color:#256EF4 !important}
.st-key-quick{background:#F4F7FC;border:1px solid #DDE1E6;border-radius:8px;padding:16px 14px}
.st-key-quick button{min-height:44px;font-weight:600;background:#fff;justify-content:flex-start}
.st-key-quick button[data-testid=stBaseButton-primary]{background:#256EF4;color:#fff}
.st-key-quick button:hover{border-color:#256EF4;color:#256EF4}
h2,h3{color:#063A74}
.lead{font-size:1.12rem;margin:-6px 0 4px 0}
.score{font-size:2.8rem;font-weight:700;line-height:1}
.svc{font-size:1.1rem;font-weight:700;margin:2px 0 0 0;line-height:1.4;min-height:2.8em;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.money-line{font-size:1.02rem;font-weight:700;color:#063A74;margin:2px 0;min-height:1.5em}
.what{font-size:.95rem;color:#464C53;line-height:1.55;margin:0 0 4px 0;height:3.1em;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.easy{border:1px solid #DDE1E6;border-left:4px solid #256EF4;border-radius:8px;padding:14px 18px;margin-bottom:10px;background:#fff;font-size:1.08rem;line-height:1.7}
.easy h4{margin:0 0 4px 0;font-size:.92rem;color:#063A74}
.sum{border:1px solid #DDE1E6;border-top:3px solid #256EF4;border-radius:8px;padding:12px 14px;background:#fff;font-size:.98rem;line-height:1.6;height:100%}
.insight{font-size:.92rem;color:#1E2124;background:#F4F7FC;border-left:3px solid #256EF4;padding:6px 10px;border-radius:4px;margin:2px 0 6px}
.sum h4{margin:0 0 4px 0;font-size:.88rem;color:#063A74}
.sum .money,.money{color:#D6403A;font-size:1.05rem}
.dlg-t{font-size:1.35rem;font-weight:700;color:#1E2124;line-height:1.35}
.dlg-sub{font-size:.88rem;color:#6D7882;margin:4px 0 2px}
.band{font-weight:700;padding:1px 8px;border-radius:6px}
.band-낮음{color:#2F8F5B;background:#E8F5EE}.band-보통{color:#B86A1E;background:#FDF1E4}.band-높음{color:#D6403A;background:#FDECEB}
[data-testid=stDialog] [role=dialog]{padding-top:.4rem}
section[data-testid=stSidebar]{width:232px !important;min-width:232px !important}
[data-testid=stSidebar]{background:#F4F7FC;border-right:1px solid #DDE1E6}
[data-testid=stSidebarHeader] img{height:2.6rem !important}
</style>""", unsafe_allow_html=True)
st.markdown("<div class='gov-top'>공공데이터로 만든 빅데이터실습 학생 프로젝트이며 정부 공식 서비스가 아닙니다</div>",
            unsafe_allow_html=True)

# 문턱 구간: (분석용 이름, 시민용 이름, 하한, 상한, 배지 색, 차트 색)
BANDS = [("낮음", "신청 쉬움", 0, 40, "green", "#2F8F5B"), ("보통", "신청 보통", 40, 60, "orange", "#D9822B"),
         ("높음", "신청 까다로움", 60, 101, "red", "#D6403A")]
LABELS = {"no_online": "온라인 신청 불가", "visit": "방문 필수", "agency": "기관 1곳당",
          "condition": "조건 1개당", "document": "서류 1개당", "readability": "난이도 1점당"}


def band(s: int):
    return next(b for b in BANDS if b[2] <= s < b[3])


@st.cache_data(show_spinner="복지 정보를 불러오고 있어요")
def services():
    return load_all()


def fail(msg, err=None):
    """사용자에게는 할 일을 알려 주고, 기술 내용은 접어 둔다."""
    st.error(msg, icon=":material/error:")
    if st.button("처음 화면으로", icon=":material/refresh:", key="fail_reset"):
        st.session_state.clear()
        st.rerun()
    if err is not None:
        with st.expander("개발자용 오류 내용"):
            st.exception(err)


DEMO = st.query_params.get("service", "")  # 시연 링크: ?service=주거약자 이사지원
if "W" not in st.session_state:
    st.session_state.W = dict(score.DEFAULT_WEIGHTS)
W = st.session_state.W
try:
    SV = services()
    ROWS = build(W, "규칙", SV)
except Exception as e:  # 데이터 파일이 없거나 깨졌을 때
    fail("복지 정보를 불러오지 못했어요. 잠시 후 새로고침해 주세요. 계속되면 data 폴더를 확인해 주세요.", e)
    st.stop()
if not SV:
    fail("불러온 복지 정보가 없어요. data 폴더에 수집 파일이 있는지 확인해 주세요.")
    st.stop()


# ── 공통 조각 ──────────────────────────────────────────
# 받는 단위(연간·월·최대·1인당 등)가 붙은 금액만 핵심 혜택으로 뽑는다. '76,000천원' 같은 예산 숫자는 거른다.
MONEY = re.compile(r"(?:(?:1인당|가구당|학기당|연간|연|매월|월|최대)\s*)+[\d,.]+\s*(?:만\s*|천\s*|억\s*)?원(?:/월|까지)?")


def benefit(text: str) -> str:
    m = MONEY.search(text or "")
    return m.group(0) if m else ""


def easy_cards(r):
    s, ai = r["svc"], r["ai"]
    if not ai:
        st.info("아직 쉬운 설명이 없습니다.")
        return
    e = ai["easy"]
    how = ("온라인 신청 가능" if r["ext"]["online"] else "방문 신청") + (f"<br>{e['steps'][0]}" if e["steps"] else "")
    money = benefit(e["what"])
    c = st.columns(3)  # 대상 / 지원 내용 / 신청 방법을 먼저 한눈에
    c[0].markdown(f"<div class='sum'><h4>누가</h4>{e['who']}</div>", unsafe_allow_html=True)
    c[1].markdown(f"<div class='sum'><h4>무엇을</h4>{f'<b class=money>{money}</b><br>' if money else ''}{e['what']}</div>", unsafe_allow_html=True)
    c[2].markdown(f"<div class='sum'><h4>어떻게 신청</h4>{how}</div>", unsafe_allow_html=True)
    st.markdown("<div class='easy'><h4>조건</h4>" + "<br>".join(f"✔ {c}" for c in e["conditions"]) + "</div>", unsafe_allow_html=True)
    st.markdown("<div class='easy'><h4>할 일</h4>" + "<br>".join(f"{i}. {x}" for i, x in enumerate(e["steps"], 1)) + "</div>", unsafe_allow_html=True)
    chk = numcheck.check(llm.source_text(s), llm.easy_text(e))
    if chk["ok"]:
        st.caption(f"✅ 원문 숫자 {chk['total']}개가 모두 들어 있는지 확인했어요.")
    else:
        st.warning("사람 확인 필요 — 빠진 숫자: " + ", ".join(chk["missing"]))


def docs_list(r):
    """서류 준비: 무엇을, 어디서, 방문이 어려우면 어떻게."""
    s = r["svc"]
    docs, listed = paperwork.documents(r)
    if listed:
        st.markdown("**이 복지에 필요한 서류**")
    else:
        st.info("안내문에 서류 목록이 없어요. 아래는 대부분의 복지 신청에 기본으로 필요한 서류예요. "
                "정확한 목록은 첨부 공고문이나 담당 기관에서 확인하세요.")
        st.markdown("**기본으로 챙길 서류**")
    for i, d in enumerate(docs):
        w = paperwork.where(d)
        st.checkbox(d, key=f"doc_{s['servId']}_{i}",
                    help=f"{w[0]}{' · 온라인 발급 가능' if w[1] else ''}" if w else "담당 기관에 물어보세요")
    rows = [{"서류": d, "어디서 떼나요": w[0], "온라인": "가능" if w[1] else "-"} for d in docs if (w := paperwork.where(d))]
    if rows:
        st.markdown("**어디서 떼나요**")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    files = s.get("form_files", [])
    if files:
        st.markdown("**기관 첨부 파일**")
        for f in files:
            c = st.columns([4, 1], vertical_alignment="center")
            c[0].markdown(f"{'📝 작성 양식' if packet.is_form(f['name']) else '📄 참고 자료'} · {f['name']}")
            c[1].link_button("받기", f["url"], width="stretch")
    packet_box(r)
    with st.expander("방문이 어렵다면 이렇게 전화해 보세요"):
        st.code(paperwork.call_script(s), language=None, wrap_lines=True)
    c = st.columns(2, vertical_alignment="center")
    if s.get("contact"):
        c[0].markdown(f"📞 문의 **{s['contact']}**")
    if s.get("link"):
        c[1].link_button("복지로에서 신청 안내 보기", s["link"], width="stretch")


def packet_box(r):
    """상담에서 받은 정보로 채운 작성 도우미 PDF + 원본 양식을 ZIP으로."""
    s, ss = r["svc"], st.session_state
    me = ss.get("me")
    with st.container(border=True):
        st.markdown("**📦 신청 준비 꾸러미 한 번에 받기**")
        st.caption("신청 준비표, 미리 채운 신청서·위임장 작성 도우미(PDF), 복지로 원본 양식을 ZIP 하나로 드려요. "
                   "주민등록번호·서명·계좌처럼 본인만 쓸 수 있는 칸은 비워 둬요. 입력한 내용은 저장하지 않아요.")
        if me:
            st.markdown("채울 정보: " + " · ".join(([me["name"] + "님"] if me["name"] else []) + intake.summary(me)))
        else:
            st.info("'내 복지 찾기'에서 질문에 답하면 주소·나이 등이 자동으로 채워져요. 지금 받으면 빈칸으로 나와요.")
        c = st.columns(2)
        name = c[0].text_input("이름 (선택)", key=f"pk_name_{s['servId']}", placeholder="예: 홍길동",
                               help="신청서·위임장 작성 도우미의 성명 칸에만 넣고 저장하지 않아요.")
        if name.strip():
            me = {**(me or intake.new_profile()), "name": name.strip()[:10]}
        extra = {"phone": c[1].text_input("연락처 (선택)", key=f"pk_phone_{s['servId']}"),
                 "address": st.text_input("상세 주소 (선택)", key=f"pk_addr_{s['servId']}", placeholder="예: OO로 12, 101동 202호")}
        c = st.columns(2)
        extra |= {"proxy": c[0].text_input("대신 내는 사람 성함 (선택)", key=f"pk_proxy_{s['servId']}"),
                  "relation": c[1].text_input("신청인과 관계 (선택)", key=f"pk_rel_{s['servId']}", placeholder="예: 아들")}
        if st.button("꾸러미 만들기", type="primary", key=f"pk_make_{s['servId']}", width="stretch"):
            with st.spinner("작성 도우미를 채우고 복지로에서 양식을 내려받고 있어요"):
                try:
                    ss[f"pk_{s['servId']}"] = packet.build_zip(r, me, extra)
                except Exception as e:  # 복지로 접속 실패 등
                    ss.pop(f"pk_{s['servId']}", None)
                    st.error("꾸러미를 만들지 못했어요. 인터넷 연결을 확인하고 다시 눌러 주세요.", icon=":material/error:")
                    with st.expander("개발자용 오류 내용"):
                        st.exception(e)
        if made := ss.get(f"pk_{s['servId']}"):
            data, failed = made
            st.download_button("ZIP 내려받기", data, file_name=f"신청준비_{s['servNm'][:30]}.zip", mime="application/zip",
                               icon=":material/download:", width="stretch", key=f"pk_dl_{s['servId']}")
            if failed:
                st.warning(f"원본 {len(failed)}개는 복지로에서 받지 못해 주소만 넣었어요: " + ", ".join(failed))


def score_parts(r):
    parts = pd.DataFrame({"항목": list(r["parts"]), "점수": list(r["parts"].values())})
    st.altair_chart(alt.Chart(parts).mark_bar(cornerRadiusEnd=4, color="#256EF4").encode(
        x=alt.X("점수:Q", title=None), y=alt.Y("항목:N", sort=None, title=None)).properties(height=220))
    e = r["ext"]
    st.markdown(f"**조건 {len(e['conditions'])}개** · " + ", ".join(c["type"] for c in e["conditions"]) if e["conditions"] else "**조건** 없음")
    st.markdown(f"**거치는 기관** {' → '.join(e['agencies']) or '없음'}")
    st.markdown(f"**안내문 난이도** {r['read']['points']}점 (문장 평균 {r['read']['avg_len']}자, 어려운 말: {', '.join(r['read']['hard_words']) or '없음'})")
    st.caption(f"기준표 계산 · 원점수 {r['raw']} · AI를 쓰지 않음")


@st.dialog("신청 안내", width="large")
def detail(r):
    """시민용: 쉬운 설명과 챙길 것만 앞에, 점수·원문은 뒤 탭에."""
    s = r["svc"]
    b = band(r["score"])
    st.markdown(f"<div class='dlg-t'>{s['servNm']}</div><div class='dlg-sub'>{s['org']} · {s['region']} · "
                f"<span class='band band-{b[0]}'>{b[1]} · 문턱 {r['score']}점</span></div>", unsafe_allow_html=True)
    t1, t2, t3, t4 = st.tabs(["쉬운 설명", "서류 준비", "왜 이 점수?", "원문"])
    with t1:
        easy_cards(r)
    with t2:
        docs_list(r)
    with t3:
        st.write(r["reason"])
        score_parts(r)
    with t4:
        for k, t in (("target", "지원대상"), ("criteria", "선정기준"), ("benefit", "지원내용"), ("apply", "신청방법")):
            st.markdown(f"**{t}**")
            st.text(s[k] or "(비어 있음)")


def card(r, key, why=None):
    s = r["svc"]
    b = band(r["score"])
    what = r["ai"]["easy"]["what"] if r["ai"] else s["summary"]
    money = benefit(what)
    with st.container(border=True):
        st.badge(b[1], color=b[4])
        st.markdown(f"<div class='svc'>{s['servNm']}</div><div class='money-line'>{f'💰 {money}' if money else '&nbsp;'}</div>"
                    f"<div class='what'>{what}</div>", unsafe_allow_html=True)
        st.caption(" · ".join(why) if why else s["region"])
        if st.button("신청 방법 보기", key=key, width="stretch"):
            detail(r)


def grid(items, prefix, n=3):
    """items: [(row, why)]"""
    for i in range(0, len(items), n):
        for col, (r, why) in zip(st.columns(n), items[i:i + n]):
            with col:
                card(r, f"{prefix}_{r['svc']['servId']}", why)


# ── 페이지 ────────────────────────────────────────────
PLACES = sorted({(s["sido"], s["sgg"]) for s in SV if s["src"] == "지자체" and s["sido"]})
FORM_Q = {"place": "살고 계신 지역은 어디세요?", "age": "나이가 어떻게 되세요?", "job": "직업은 무엇인가요?",
          "family": "함께 사는 가족은 어떻게 되세요?", "special": "해당되는 것이 있나요?", "need": "요즘 가장 도움이 필요한 건 뭔가요?"}


def form_widget(key, quick):
    """질문 하나의 입력칸. 답을 글로 돌려줘 상담과 같은 intake.take()로 읽는다."""
    ss = st.session_state
    if key == "place":
        sido = st.selectbox("시·도", ["선택 안 함"] + sorted({s for s, _ in PLACES}), key="f_sido")
        sggs = sorted({g for s, g in PLACES if s == sido and g})
        if ss.get("f_sgg") not in ["전체"] + sggs:
            ss.f_sgg = "전체"
        sgg = st.selectbox("시·군·구", ["전체"] + sggs, key="f_sgg", disabled=sido == "선택 안 함")
        st.caption("시연 샘플에 있는 지역만 나와요. 고르지 않으면 전국 복지만 찾아요.")
        return "없음" if sido == "선택 안 함" else f"{sido} {'' if sgg == '전체' else sgg}"
    if key == "age":
        age = st.number_input("나이", 0, 120, value=None, step=1, key="f_age", placeholder="예: 32", label_visibility="collapsed")
        return "" if age is None else f"{int(age)}세"
    multi = key != "job"
    pick = st.pills(key, quick, selection_mode="multi" if multi else "single", key=f"f_{key}", label_visibility="collapsed")
    picks = (pick or []) if multi else ([pick] if pick else [])
    if intake.ETC in picks:  # 기타를 고르면 직접 적는다
        etc = st.text_input("직접 입력", key=f"f_{key}_etc", placeholder="자유롭게 적어 주세요")
        picks = [x for x in picks if x != intake.ETC] + ([etc] if etc.strip() else [])
    st.caption("여러 개 골라도 돼요. 없으면 그냥 넘어가세요." if multi else "가장 가까운 것 하나를 골라 주세요.")
    return ", ".join(picks) or "없음"


def steps():
    """상담과 같은 질문을 한 장씩 보여 주고, 끝나면 같은 분석 결과를 보여 준다."""
    ss = st.session_state
    ss.setdefault("fi", 0)
    ss.setdefault("form", {})  # 질문 키 → 답 글
    ss.setdefault("form_raw", {})  # 위젯 키 → 위젯 값(페이지를 오가도 유지)
    n = len(intake.QUESTIONS)
    if ss.fi >= n:
        top = st.columns([4, 1])
        if top[1].button("다시 고르기", width="stretch"):
            ss.fi, ss.form, ss.form_raw = 0, {}, {}
            ss.pop("quick", None)
            ss.pop("quick_name", None)
            st.rerun()
        p = intake.build(ss.form, PLACES)
        if q := ss.get("quick"):  # 자주 찾는 복지 버튼으로 바로 온 경우
            p.update(q)
        report(p)
        return
    for k, v in ss.form_raw.items():
        if k not in ss:
            ss[k] = v
    ss.fi = max(ss.fi, 0)
    key, question, quick = intake.QUESTIONS[ss.fi]
    st.progress((ss.fi + 1) / n, text=f"{ss.fi + 1} / {n}")
    with st.container(border=True):
        # 질문 목록이 바뀌어도 깨지지 않게, 화면용 문구가 없으면 상담 질문 문장을 그대로 쓴다
        title = FORM_Q.get(key) or re.sub(r"\*\*|\{name\}님,? ?", "", question.split("\n\n")[-1])
        st.markdown(f"<div class='q'>{title}</div>", unsafe_allow_html=True)
        text = form_widget(key, quick)
        err = st.empty()
    b = st.columns([1, 3, 1])  # 이전은 왼쪽, 다음은 질문 카드 오른쪽 끝에 맞춘다
    if ss.fi > 0 and b[0].button("← 이전", width="stretch"):
        ss.fi -= 1
        st.rerun()
    if b[2].button("결과 보기" if ss.fi == n - 1 else "다음 →", type="primary", width="stretch"):
        if again := intake.take(intake.new_profile(), key, text, PLACES):
            err.error(again)
        else:
            ss.form[key] = text
            ss.form_raw.update({k: ss[k] for k in ss if k.startswith("f_")})
            ss.fi += 1
            st.rerun()


def report(p):
    """상담 답으로 찾은 결과를 분석표처럼 보여 준다."""
    st.session_state.me = p  # 서류 준비 꾸러미에 채울 정보
    c = intake.conditions(p)
    recs = match.recommend(ROWS, **c)
    # 태그가 하나도 안 맞은 것(대상 태그가 비어 누구에게나 걸리는 서비스)은 따로 접어 둔다
    hit, loose = [x for x in recs if x[1]], [x for x in recs if not x[1]]
    if not hit:
        hit, loose = loose, []
    quick = st.session_state.get("quick_name")
    who = f"{quick} 대상 복지" if quick else f"{p['name'] + '님 ' if p['name'] else ''}맞춤 복지"
    st.markdown(f"### {who} {len(hit)}개")
    st.caption(" · ".join(([] if quick else intake.summary(p))) or "질문 없이 대상으로만 고른 결과예요. 지역을 고르면 동네 복지도 함께 찾아요.")
    if not recs:
        st.warning("말씀하신 조건에 맞는 복지를 샘플에서 찾지 못했어요. '다시 고르기'를 눌러 조건을 바꿔 보세요.")
        return
    rs = [r for r, _ in hit]
    stats = [("신청이 쉬운 것", sum(band(r["score"])[0] == "낮음" for r in rs)),
             ("온라인 신청 가능", n_online := sum(r["ext"]["online"] for r in rs)),
             ("우리 동네 복지", sum(r["svc"]["src"] == "지자체" for r in rs))]
    stats = [("찾은 복지", len(rs))] + [x for x in stats if x[1]]  # 0인 칸은 숨긴다
    for col, (label, n) in zip(st.columns(4), stats):
        col.metric(label, f"{n}개", border=True)
    easiest = rs[0] if band(rs[0]["score"])[0] == "낮음" else min(rs, key=lambda r: r["score"])
    st.info(f"**{easiest['svc']['servNm']}**부터 알아보세요. 조건이 잘 맞고 신청 문턱이 {easiest['score']}점으로 낮은 편이에요."
            + ("" if n_online else " 온라인으로 신청할 수 있는 복지는 없어서 주민센터 방문이 필요해요."))
    grid(hit[:4], "rec", n=2)
    if len(hit) > 4:
        with st.expander(f"나머지 {len(hit) - 4}개 더 보기"):
            grid(hit[4:], "rec_more", n=2)
    if loose:
        with st.expander(f"대상 구분 없이 누구나 볼 수 있는 복지 {len(loose)}개"):
            grid(loose, "rec_loose", n=2)
    st.caption("공식 목록의 지역·생애주기·대상 태그로 고른 결과예요. 실제 자격은 담당 기관에서 확인해요.")


def chat():
    """상담처럼 하나씩 물어본다. 답은 빠른 답 버튼이나 직접 입력으로 받는다."""
    ss = st.session_state
    if "prof" not in ss:
        ss.prof, ss.qi = intake.new_profile(), 0
        ss.msgs = [{"role": "assistant", "content": intake.QUESTIONS[0][1]}]
    p = ss.prof
    head = st.columns([4, 1], vertical_alignment="center")
    head[0].progress(min(ss.qi, len(intake.QUESTIONS)) / len(intake.QUESTIONS),
                     text=f"질문 {min(ss.qi + 1, len(intake.QUESTIONS))} / {len(intake.QUESTIONS)}" if ss.qi < len(intake.QUESTIONS) else "상담 완료")
    if head[1].button("처음부터", width="stretch"):
        del ss.prof
        st.rerun()
    with st.container(height=380, border=True, key="chatbox"):  # AI는 왼쪽, 사용자는 오른쪽 말풍선
        for m in ss.msgs:
            st.chat_message(m["role"], avatar=str(LOGO / "mark.svg") if m["role"] == "assistant" else None).markdown(m["content"])

    def answer(text):
        key = intake.QUESTIONS[ss.qi][0]
        ss.msgs.append({"role": "user", "content": text})
        if text == intake.ETC:
            ss.msgs.append({"role": "assistant", "content": "편하게 직접 적어 주세요. 아래 입력칸에 쓰시면 돼요."})
            return
        if again := intake.take(p, key, text, PLACES):
            ss.msgs.append({"role": "assistant", "content": again})
            return
        ss.qi += 1
        if ss.qi < len(intake.QUESTIONS):
            ss.msgs.append({"role": "assistant", "content": intake.QUESTIONS[ss.qi][1].format(name=p["name"])})
        else:
            n = len(match.recommend(ROWS, **intake.conditions(p)))
            ss.msgs.append({"role": "assistant", "content": f"{p['name'] + '님 ' if p['name'] else ''}말씀을 정리했어요. 받을 수 있을 만한 복지 **{n}개**를 찾았어요. 아래 분석 결과를 봐 주세요."})

    if ss.qi < len(intake.QUESTIONS):
        key, _, quick = intake.QUESTIONS[ss.qi]
        if key == "place":  # 빠른 답: 샘플에 있는 지역
            quick = sorted({g for _, g in PLACES if g})[:6]
        if quick:
            pick = st.pills("빠른 답", quick, key=f"quick_{ss.qi}_{len(ss.msgs)}", label_visibility="collapsed")
            if pick:
                answer(pick)
                st.rerun()
        if text := st.chat_input("여기에 답을 적어 주세요 (빠른 답에 없으면 직접 적으셔도 돼요)"):
            answer(text)
            st.rerun()
    else:
        report(p)


QUICK = [("청년", ":material/school:", {"life": ["청년"]}), ("어르신", ":material/elderly:", {"life": ["노년"]}),
         ("임신·출산", ":material/pregnant_woman:", {"life": ["임신·출산"]}), ("아이 키우기", ":material/child_care:", {"life": ["영유아", "아동"]}),
         ("장애인", ":material/accessible:", {"groups": ["장애인"]}), ("저소득", ":material/savings:", {"groups": ["저소득"]})]


# 내부 값은 그대로 두고 보이는 이름만 바꾼다(상담은 OpenAI 연결 예정)
MODES = {"질문에 답하기": ":material/checklist: 하나씩 골라서 찾기", "상담으로 찾기": ":material/smart_toy: AI와 대화로 찾기"}


def page_head(title, crumb, notice=""):
    st.markdown(f"<div class='crumb'>홈 › {crumb}</div><div class='page-t'>{title}</div>"
                + (f"<div class='notice'>{notice}</div>" if notice else ""), unsafe_allow_html=True)


def quick_start(name, c):
    ss = st.session_state
    ss.fi, ss.form, ss.form_raw, ss.quick, ss.quick_name, ss.mode = len(intake.QUESTIONS), {}, {}, c, name, "질문에 답하기"


STEPS = [(":material/checklist:", "질문에 답하기", "지역·나이 등 6가지를 골라요"),
         (":material/fact_check:", "맞춤 복지 확인", "조건이 맞고 신청이 쉬운 순서로"),
         (":material/description:", "서류 준비", "신청서 작성 도우미까지 한 번에")]


def home():
    """홈: 정부24처럼 서비스 이름표, 토스처럼 말하듯 한 문장, 애플처럼 여백과 버튼 하나."""
    ss = st.session_state
    n = len(intake.QUESTIONS)
    with st.container(key="home_hero"):
        st.markdown("<div class='home-tag'>복지 서비스 찾기</div>"
                    "<div class='home-t'>내가 받을 수 있는 복지,<br>질문 몇 개면 알 수 있어요</div>"
                    f"<div class='home-s'>지역과 나이, 가족 상황을 고르면 복지 {len(SV)}개 중 조건이 맞는 것만 골라 드려요.</div>",
                    unsafe_allow_html=True)
        with st.container(horizontal=True, vertical_alignment="center", gap="medium"):  # 버튼과 링크를 나란히
            if st.button("내 복지 찾기", type="primary", key="home_start"):
                ss.mode, ss.fi, ss.form, ss.form_raw = "질문에 답하기", 0, {}, {}  # 시작은 늘 첫 질문부터
                ss.pop("quick", None)
                ss.pop("quick_name", None)
                st.switch_page(FIND)
            if st.button("전체 서비스 보기 ›", type="tertiary", key="home_all"):
                st.switch_page(ALL)
        st.markdown(f"<div class='home-meta'>질문 {n}개 · 시연용 샘플 {len(SV)}개 서비스로 동작 · 입력한 내용은 저장하지 않아요</div>",
                    unsafe_allow_html=True)
    if demo := next((r for r in ROWS if r["svc"]["servNm"] == DEMO), None):
        st.markdown("<div class='sec-t'>시연 서비스</div>", unsafe_allow_html=True)
        grid([(demo, None)], "demo", n=2)
    st.markdown("<div class='sec-t'>누구를 위한 복지를 찾으세요?</div>", unsafe_allow_html=True)
    with st.container(key="home_quick"):
        for col, (name, icon, c) in zip(st.columns(len(QUICK)), QUICK):
            if col.button(name, icon=icon, width="stretch", key=f"home_q_{name}"):
                quick_start(name, c)
                st.switch_page(FIND)
    st.markdown("<div class='sec-t'>이렇게 진행돼요</div>", unsafe_allow_html=True)
    st.markdown("<div class='steps'>" + "".join(
        f"<div><span class='n'>{i}</span><b>{t}</b><p>{d}</p></div>" for i, (_, t, d) in enumerate(STEPS, 1)) + "</div>",
        unsafe_allow_html=True)


def mine():
    """첫 화면: 서비스 이름보다 '어떻게 시작하면 되는지'를 먼저, 첫 질문까지 한 화면에."""
    ss = st.session_state
    ss.setdefault("mode", "질문에 답하기")
    n = len(intake.QUESTIONS)
    done = ss.get("qi", 0) >= n if ss.mode == "상담으로 찾기" else ss.get("fi", 0) >= n
    # 정부24·복지로처럼 위치 표시 + 밑줄 제목. 이름은 사이드바 로고에 있으니 '무엇을 하면 되는지'를 제목으로
    page_head("내가 받을 수 있는 복지" if done else f"질문 {n}개로 받을 수 있는 복지 찾기", "내 복지 찾기",
              f"ⓘ 시연용 샘플 <b>{len(SV)}개 서비스</b> 안에서 찾아요")
    left, right = st.columns([3, 1], gap="large")
    with left:
        mode = st.segmented_control("방법", list(MODES), key="mode", label_visibility="collapsed", width="stretch",
                                    format_func=MODES.get)
        if mode == "상담으로 찾기":
            chat()
        else:
            steps()
    with right, st.container(key="quick"):
        st.markdown("**생애주기·대상별 복지**")
        st.caption("질문 없이 바로 보기")
        picked = ss.get("quick_name") if done and ss.mode == "질문에 답하기" else None
        for name, icon, c in QUICK:  # 바로 결과로. mode 위젯이 먼저 그려지므로 콜백에서 바꾼다
            st.button(name, icon=icon, width="stretch", key=f"quick_{name}", on_click=quick_start, args=(name, c),
                      type="primary" if name == picked else "secondary")


def browse():
    page_head("전체 서비스", "전체 서비스")
    with st.container(border=True):
        q = st.text_input("검색", value=DEMO, placeholder="서비스 이름, 기관, 지역으로 찾기 (예: 청년, 장애, 해남)", label_visibility="collapsed")
        f = st.columns([2, 3, 2, 2], vertical_alignment="center")
        src = f[0].pills("구분", ["중앙", "지자체"], selection_mode="multi", default=["중앙", "지자체"])
        lv = f[1].segmented_control("신청 난이도", ["전체"] + [b[1].replace("신청 ", "") for b in BANDS], default="전체")
        order = f[2].selectbox("정렬", ["신청 쉬운 순", "신청 어려운 순", "이름순"])
        online = f[3].toggle("온라인 신청 가능만")
    rows = [r for r in ROWS if r["svc"]["src"] in src
            and (lv in (None, "전체") or band(r["score"])[1].endswith(lv))
            and (not online or r["ext"]["online"])
            and (not q or q in r["svc"]["servNm"] + r["svc"]["org"] + r["svc"]["region"] + r["svc"]["summary"])]
    if order == "신청 쉬운 순":
        rows = rows[::-1]
    elif order == "이름순":
        rows = sorted(rows, key=lambda r: r["svc"]["servNm"])
    st.caption(f"{len(rows)}건")
    if rows:
        grid([(r, None) for r in rows], "all")
    else:
        st.info("조건에 맞는 서비스가 없어요. 검색어나 필터를 바꿔 보세요.")


def weights():
    with st.expander("가중치 조절 (팀 결정 전 안)", icon=":material/tune:"):
        c = st.columns(3)
        for i, k in enumerate(score.DEFAULT_WEIGHTS):
            if f"_w_{k}" not in st.session_state:  # 다른 페이지에 다녀와도 값 유지
                st.session_state[f"_w_{k}"] = float(W[k])
            c[i % 3].slider(LABELS[k], 0.0, 5.0, step=0.5, key=f"_w_{k}")
        if st.button("기본값으로"):
            st.session_state.W = dict(score.DEFAULT_WEIGHTS)
            for k in score.DEFAULT_WEIGHTS:
                del st.session_state[f"_w_{k}"]
            st.rerun()
        new = {k: st.session_state[f"_w_{k}"] for k in score.DEFAULT_WEIGHTS}
        if new != W:
            st.session_state.W = new
            st.rerun()


def analysis():
    page_head("데이터 분석", "팀·평가용 › 데이터 분석", "문턱 점수는 AI를 쓰지 않고 정해 둔 기준표로 계산합니다.")
    t1, t2, t3 = st.tabs(["요약", "점수 상세", "검증"])
    # 항목 점수도 문턱 점수와 같은 100점 환산으로 맞춘다(원점수와 섞지 않게)
    scaled = [{k: v * r["score"] / r["raw"] if r["raw"] else 0 for k, v in r["parts"].items()} for r in ROWS]
    df = pd.DataFrame([{"점수": r["score"], "구분": r["svc"]["src"], "온라인": r["ext"]["online"],
                        "구간": band(r["score"])[0], **p} for r, p in zip(ROWS, scaled)])
    items = list(ROWS[0]["parts"])
    with t1:
        have = [r for r in ROWS if r["ai"]]
        ok = sum(not r["ai"]["needs_review"] for r in have)
        c = st.columns(4)
        c[0].metric("분석한 복지서비스", f"{len(df)}건", border=True)
        c[1].metric("평균 문턱 점수", f"{df['점수'].mean():.0f}점", border=True)
        c[2].metric("온라인 신청 불가", f"{(~df['온라인']).mean() * 100:.0f}%", border=True)
        c[3].metric("쉬운 설명 숫자 검사 통과", f"{ok}/{len(have)}", border=True)
        left, right = st.columns(2)
        cnt = df["구간"].value_counts()
        with left.container(border=True):
            st.markdown("**문턱 점수 분포**")
            st.markdown(f"<div class='insight'>{len(df)}개 중 <b>{cnt.get('높음', 0)}개</b>가 신청 까다로움(60점 이상), "
                        f"{cnt.get('낮음', 0)}개가 신청 쉬움(40점 미만)이에요.</div>", unsafe_allow_html=True)
            st.altair_chart(alt.Chart(df).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                x=alt.X("점수:Q", bin=alt.Bin(step=5), title="문턱 점수"), y=alt.Y("count():Q", title="서비스 수"),
                color=alt.Color("구간:N", scale=alt.Scale(domain=[b[0] for b in BANDS], range=[b[5] for b in BANDS]),
                                legend=alt.Legend(orient="top", title=None))).properties(height=260))
        with right.container(border=True):
            st.markdown("**무엇이 문턱을 높이나 · 구분별 항목 평균**")
            avg = df.groupby("구분")[items].mean()
            st.markdown("<div class='insight'>" + ", ".join(f"{g}은 <b>{avg.loc[g].idxmax()}</b>" for g in avg.index)
                        + " 항목이 문턱을 가장 많이 올려요.</div>", unsafe_allow_html=True)
            parts = avg.reset_index().melt("구분", var_name="항목", value_name="평균 점수")
            st.altair_chart(alt.Chart(parts).mark_bar().encode(
                x=alt.X("평균 점수:Q", stack="zero", title="평균 문턱 점수(100점 환산)"), y=alt.Y("구분:N", title=None),
                color=alt.Color("항목:N", legend=alt.Legend(orient="bottom", title=None, columns=3))).properties(height=260))
    with t2:
        weights()  # 접어 두고 차트에 폭을 준다
        with st.container(border=True):
            st.markdown("**문턱이 높은 서비스 15개 · 항목별 점수**")
            st.markdown(f"<div class='insight'>가장 까다로운 곳은 <b>{ROWS[0]['svc']['servNm']}</b>({ROWS[0]['score']}점)이에요. "
                        "막대 전체 길이가 문턱 점수(100점 환산)예요.</div>", unsafe_allow_html=True)
            top = pd.DataFrame([{"서비스": r["svc"]["servNm"], "항목": k, "점수": round(v, 1)}
                                for r, p in zip(ROWS[:15], scaled[:15]) for k, v in p.items()])
            st.altair_chart(alt.Chart(top).mark_bar().encode(
                x=alt.X("sum(점수):Q", title="문턱 점수(100점 환산)", scale=alt.Scale(domain=[0, 100])),
                y=alt.Y("서비스:N", sort="-x", title=None, axis=alt.Axis(labelLimit=420, labelOverlap=False, labelFontSize=12)),
                color=alt.Color("항목:N", legend=alt.Legend(orient="bottom", title=None, columns=6)),
                tooltip=["서비스", "항목", "점수"]).properties(height=520))
        st.dataframe(pd.DataFrame([{
            "점수": r["score"], "서비스": r["svc"]["servNm"], "구분": r["svc"]["src"], "지역": r["svc"]["region"],
            "이유": r["reason"], "온라인": "가능" if r["ext"]["online"] else "불가",
            "조건 수": len(r["ext"]["conditions"]), "기관 단계": len(r["ext"]["agencies"]),
            "서류 수": len(r["ext"]["documents"]) if r["ext"].get("documents") else None,
        } for r in ROWS]), hide_index=True, width="stretch",
            column_config={"점수": st.column_config.ProgressColumn("점수", min_value=0, max_value=100, format="%d")})
    with t3:
        rule_sum = {s["servId"]: evaluate.summarize(rules.extract(s)) for s in SV}
        ai_sum = {s["servId"]: evaluate.summarize(c["extract"]) for s in SV if (c := llm.load_cached(s["servId"]))}
        gold = evaluate.load_gold()
        a, b = st.columns(2)
        with a.container(border=True):
            st.markdown("**정답표와 비교 (일치율 %)**")
            if gold:
                mix = evaluate.gold_mix()
                st.caption(f"정답표 {len(gold)}건 · AI 초안을 사람이 검수 {mix['사람 검수']}건 · AI가 작성·재검토 {mix['AI 재검토']}건 (기준: eval/README.md)")
                r_cmp, a_cmp = evaluate.compare(rule_sum, gold), evaluate.compare(ai_sum, gold)
                st.dataframe(pd.DataFrame({"항목": [evaluate.LABELS[f] for f in evaluate.FIELDS],
                                           "기준표(규칙)": [r_cmp[f] for f in evaluate.FIELDS],
                                           "AI": [a_cmp[f] for f in evaluate.FIELDS]}), hide_index=True, width="stretch")
            else:
                st.info("아직 채운 정답표가 없습니다. 각자 `eval/gold_이름.csv`를 채워 올리면 여기에 정확도가 나옵니다. 기준은 `eval/README.md`.")
        with b.container(border=True):
            st.markdown("**기준표(규칙)와 AI가 같은 답을 낸 비율** (정답표 전 참고용)")
            if ai_sum:
                cmp = evaluate.compare(rule_sum, ai_sum)
                st.dataframe(pd.DataFrame({"항목": [evaluate.LABELS[f] for f in evaluate.FIELDS],
                                           "일치율(%)": [cmp[f] for f in evaluate.FIELDS]}), hide_index=True, width="stretch")


def guide():
    page_head("안내", "팀·평가용 › 안내")
    a, b = st.columns(2)
    with a.container(border=True):
        st.markdown(f"""
#### 문턱 점수 계산 기준
항목 점수를 더한 뒤 0~100으로 바꿉니다. AI를 쓰지 않습니다.

| 항목 | 점수(현재 설정) | 상한 |
|---|---|---|
| 온라인으로 신청할 수 없다 | +{W['no_online']} | |
| 꼭 방문해야 한다 | +{W['visit']} | |
| 거치는 기관(신청·조사·결정) | 1곳당 +{W['agency']} | {score.CAPS['agency']}곳 |
| 받을 조건(나이·소득·거주지 등) | 1개당 +{W['condition']} | {score.CAPS['condition']}개 |
| 내야 하는 서류 | 1개당 +{W['document']} (글에 없으면 0) | {score.CAPS['document']}개 |
| 안내문 난이도 | 0~3점 × {W['readability']} | 3점 |

40점 미만 **신청 쉬움**, 40~59점 **보통**, 60점 이상 **까다로움**
""")
    with b.container(border=True):
        st.markdown("""
#### 내 복지 찾기
복지서비스 목록에 공식으로 붙어 있는 지역·생애주기·대상(장애인, 저소득 등)·관심 주제 태그로 거릅니다.
자격을 판정하지 않으며, 실제 자격은 담당 기관에서 확인합니다. 입력한 값은 저장하지 않습니다.

#### 쉬운 설명 검사
원문 숫자(나이·%·금액·기간·횟수)가 쉬운 설명에도 있는지 대조합니다. 빠지면 '사람 확인 필요'를 붙입니다.

#### 신청 준비 (11/17 이후 구현, AI 없음)
신청서·위임장 빈칸 채우기, 서류 발급처 안내, 직권 신청 요청 문구. 접수 대행은 하지 않습니다.
""")
    st.caption(f"자료: 한국사회보장정보원 복지서비스 API(공공데이터포털, 중앙부처 15090532 · 지자체 15108347) 샘플 {len(SV)}건, 2026-11-10 수집")


FIND = st.Page(mine, title="내 복지 찾기", icon=":material/person_search:", url_path="find")
ALL = st.Page(browse, title="전체 서비스", icon=":material/list:", url_path="all")
nav = st.navigation({"시민용": [st.Page(home, title="홈", icon=":material/home:", default=True), FIND, ALL],
                      "팀·평가용": [st.Page(analysis, title="데이터 분석", icon=":material/analytics:", url_path="analysis"),
                             st.Page(guide, title="안내", icon=":material/info:", url_path="guide")]}, position="sidebar")
st.logo(str(LOGO / "lockup.svg"), size="large", icon_image=str(LOGO / "mark.svg"))
with st.sidebar:
    st.caption(f"샘플 {len(SV)}건 · 중앙 25 · 지자체 25\n\n입력한 내용은 저장하지 않아요.")
try:
    nav.run()
except Exception as e:  # 화면을 그리다 난 예외는 친절한 안내로 바꾼다(st.rerun·switch_page는 BaseException이라 통과)
    fail("화면을 보여 드리다 문제가 생겼어요. 아래 버튼을 눌러 처음부터 다시 시작해 주세요.", e)
