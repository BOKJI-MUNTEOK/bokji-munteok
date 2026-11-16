"""복지문턱 시연 화면. 실행: .venv/bin/python -m streamlit run app.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import pandas as pd
import streamlit as st

import evaluate
import llm
import numcheck
import score
from parse import load_all
from pipeline import build

st.set_page_config(page_title="복지문턱", page_icon="🚪", layout="wide")
st.markdown("""
<style>
.card{border:1px solid #ddd;border-radius:14px;padding:18px 22px;margin-bottom:12px;background:#fafafa;font-size:1.15rem;line-height:1.7}
.card h4{margin:0 0 6px 0;font-size:1rem;color:#666}
.big{font-size:2.6rem;font-weight:700}
.miss{background:#fff3cd;padding:2px 6px;border-radius:6px}
</style>""", unsafe_allow_html=True)


@st.cache_data
def services():
    return load_all()


SV = services()
DEMO = st.query_params.get("service", "")  # 시연 링크: ?service=주거약자 이사지원


def pick_index(names):
    return names.index(DEMO) if DEMO in names else 0
cached_ai = sum(llm.load_cached(s["servId"]) is not None for s in SV)

with st.sidebar:
    st.header("점수 기준")
    st.caption("문턱 점수는 AI를 쓰지 않고, 우리가 정한 기준에 따라 항목별 점수를 더해 계산합니다.")
    src = st.multiselect("구분", ["중앙", "지자체"], default=["중앙", "지자체"])
    st.subheader("가중치(팀 결정 전 안)")
    W = {}
    labels = {"no_online": "온라인 신청 불가", "visit": "방문 필수", "agency": "기관 1곳당",
              "condition": "조건 1개당", "document": "서류 1개당", "readability": "난이도 1점당"}
    for k, v in score.DEFAULT_WEIGHTS.items():
        W[k] = st.slider(labels[k], 0.0, 5.0, float(v), 0.5)
    st.caption(f"자료: 복지서비스 API 샘플 {len(SV)}건(2026-11-10 수집) · AI 신청 보조 {cached_ai}건")

rows = [r for r in build(W, "규칙", SV) if r["svc"]["src"] in src]

st.title("복지문턱")
st.caption("어떤 복지가 왜 신청하기 어려운지 기준표 점수로 보여 주고(①), AI가 어려운 안내문을 쉬운 말과 할 일로 바꿔 신청을 돕습니다(②).")
tab1, tab2, tab3, tab4, tab5 = st.tabs(["① 문턱 점수(기준표)", "② 신청 보조(AI)", "③ 신청서 채우기(설계)", "검증", "계산 기준"])

# ── ① 문턱 점수 ─────────────────────────────────────────
with tab1:
    df = pd.DataFrame([{
        "점수": r["score"], "서비스": r["svc"]["servNm"], "구분": r["svc"]["src"], "지역": r["svc"]["region"],
        "이유": r["reason"], "온라인": "가능" if r["ext"]["online"] else "불가",
        "조건 수": len(r["ext"]["conditions"]), "기관 단계": len(r["ext"]["agencies"]),
        "서류 수": len(r["ext"]["documents"]) if r["ext"].get("documents") else None,
    } for r in rows])
    c1, c2, c3 = st.columns(3)
    c1.metric("서비스 수", len(df))
    c2.metric("평균 문턱 점수", f"{df['점수'].mean():.0f}" if len(df) else "-")
    c3.metric("온라인 신청 불가", f"{(df['온라인'] == '불가').mean() * 100:.0f}%" if len(df) else "-")
    st.subheader("문턱이 높은 순")
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={"점수": st.column_config.ProgressColumn("점수", min_value=0, max_value=100, format="%d")})
    st.subheader("서비스 하나 자세히 보기")
    names = [r["svc"]["servNm"] for r in rows]
    if names:
        pick = st.selectbox("서비스", names, index=pick_index(names), key="score_pick")
        r = next(x for x in rows if x["svc"]["servNm"] == pick)
        a, b = st.columns([1, 2])
        a.markdown(f"<div class='big'>{r['score']}점</div>{r['reason']}", unsafe_allow_html=True)
        a.caption(f"기준표 계산 · 원점수 {r['raw']}")
        parts = pd.DataFrame({"항목": list(r["parts"]), "점수": list(r["parts"].values())})
        b.bar_chart(parts, x="항목", y="점수", horizontal=True)
        with st.expander("뽑힌 값 보기"):
            e = r["ext"]
            st.write("**조건**", [f"{c['type']}: {c['text']}" if c.get("text") else c["type"] for c in e["conditions"]])
            st.write("**거치는 기관**", e["agencies"])
            st.write("**서류**", e.get("documents") or "서류 정보 없음")
            st.write("**안내문 난이도**", f"{r['read']['points']}점 (문장 평균 {r['read']['avg_len']}자, 어려운 말: {', '.join(r['read']['hard_words']) or '없음'})")

# ── ② 쉬운 설명 ─────────────────────────────────────────
with tab2:
    have = [r for r in rows if r["ai"]]
    if not have:
        st.info("아직 AI 쉬운 설명이 없습니다. `python src/pipeline.py --ai`를 실행하면 여기에 카드가 나옵니다.")
    else:
        ok = sum(not r["ai"]["needs_review"] for r in have)
        rest = f" · 나머지 {len(have) - ok}건은 '사람 확인 필요'" if ok < len(have) else ""
        st.caption(f"숫자 검사 통과 {ok}/{len(have)}건{rest}")
        easy_names = [r["svc"]["servNm"] for r in have]
        pick = st.selectbox("서비스", easy_names, index=pick_index(easy_names), key="easy_pick")
        r = next(x for x in have if x["svc"]["servNm"] == pick)
        s, ai = r["svc"], r["ai"]
        left, right = st.columns(2)
        with left:
            st.subheader("원문")
            for k, t in (("target", "지원대상"), ("criteria", "선정기준"), ("benefit", "지원내용"), ("apply", "신청방법")):
                st.markdown(f"**{t}**")
                st.text(s[k] or "(비어 있음)")
        with right:
            st.subheader("쉬운 설명")
            e = ai["easy"]
            st.markdown(f"<div class='card'><h4>누가 받을 수 있나요</h4>{e['who']}</div>", unsafe_allow_html=True)
            st.markdown("<div class='card'><h4>조건</h4>" + "<br>".join(f"✔ {c}" for c in e["conditions"]) + "</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='card'><h4>무엇을 받나요</h4>{e['what']}</div>", unsafe_allow_html=True)
            st.markdown("<div class='card'><h4>할 일</h4>" + "<br>".join(f"{i}. {x}" for i, x in enumerate(e["steps"], 1)) + "</div>", unsafe_allow_html=True)
            docs = ai["extract"].get("documents")
            st.markdown("<div class='card'><h4>챙길 서류</h4>" + ("<br>".join(f"☐ {d}" for d in docs) if docs else
                        "안내문에 서류 정보가 없어요. 담당 기관에 물어보세요." + (f"<br>📞 {s['contact']}" if s.get("contact") else ""))
                        + "</div>", unsafe_allow_html=True)
            chk = numcheck.check(llm.source_text(s), llm.easy_text(e))
            if chk["ok"]:
                st.success(f"숫자 검사 통과: 원문 숫자 {chk['total']}개 모두 들어 있어요.")
            else:
                st.warning("사람 확인 필요 — 빠진 숫자: " + ", ".join(chk["missing"]))
            st.caption(f"모델 {ai.get('model')} · 시도 {ai.get('attempts')}회")

# ── ③ 신청 준비(설계) ───────────────────────────────────
with tab3:
    st.subheader("신청서·위임장 채우기 (11/17 이후 구현, AI 없음)")
    st.markdown("""
- **신청서 미리 채우기**: 사회보장급여 신청(변경)서(공통서식 별지 1호) 빈칸에 이름·주소·가구 정보를 넣어 인쇄.
- **위임장 채우기**: 가족·이웃이 대신 신청할 때 쓰는 위임장을 같은 방식으로 채움.
- **서류 체크리스트**: ①에서 뽑은 서류 목록 + 발급처(정부24, 무인발급기). 글에 없으면 "서류 정보 없음".
- **직권 신청 요청문**: 거동이 불편하면 담당 공무원에게 직권 신청을 요청하는 문구와 연락처.
- 계산이 아니라 양식 채우기라 알고리즘이 없습니다. 개인정보는 기기 안에서만 처리합니다.
- **접수 대행은 하지 않습니다**: 법상 신청권자가 아니고, 본인인증을 넘길 수 없고, 접수 API가 없습니다.
""")

# ── 규칙 vs AI ─────────────────────────────────────────
with tab4:
    import rules
    rule_sum = {s["servId"]: evaluate.summarize(rules.extract(s)) for s in SV}
    ai_sum = {s["servId"]: evaluate.summarize(c["extract"]) for s in SV if (c := llm.load_cached(s["servId"]))}
    gold = evaluate.load_gold()
    if gold:
        st.subheader(f"정답표 {len(gold)}건과 비교 (일치율 %)")
        st.caption("기준표(규칙): 점수 계산에 쓰는 값이 맞는지 확인. AI: 신청 보조의 서류·조건 안내가 맞는지 확인하고, 단순 규칙보다 얼마나 정확한지 보여 줍니다.")
        r_cmp, a_cmp = evaluate.compare(rule_sum, gold), evaluate.compare(ai_sum, gold)
        st.dataframe(pd.DataFrame({
            "항목": [evaluate.LABELS[f] for f in evaluate.FIELDS],
            "기준표(규칙)": [r_cmp[f] for f in evaluate.FIELDS],
            "AI": [a_cmp[f] for f in evaluate.FIELDS],
        }), hide_index=True)
    else:
        st.info("아직 채운 정답표가 없습니다. 각자 `eval/gold_이름.csv`를 채워 올리면 여기에 정확도가 나옵니다. 기준은 `eval/README.md`.")
    if ai_sum:
        st.subheader("기준표(규칙)와 AI가 같은 답을 낸 비율 (정답표 전 참고용)")
        cmp = evaluate.compare(rule_sum, ai_sum)
        st.dataframe(pd.DataFrame({"항목": [evaluate.LABELS[f] for f in evaluate.FIELDS],
                                   "일치율(%)": [cmp[f] for f in evaluate.FIELDS]}), hide_index=True)

# ── 계산 기준 ──────────────────────────────────────────
with tab5:
    st.markdown(f"""
**문턱 점수 = 항목 점수의 합 → 0~100으로 환산** (AI를 쓰지 않고 우리가 정한 기준표로 계산)

| 항목 | 점수(현재 설정) | 상한 |
|---|---|---|
| 온라인으로 신청할 수 없다 | +{W['no_online']} | |
| 꼭 방문해야 한다 | +{W['visit']} | |
| 거치는 기관(신청·조사·결정) | 1곳당 +{W['agency']} | {score.CAPS['agency']}곳 |
| 받을 조건(나이·소득·거주지·가구 형태 등) | 1개당 +{W['condition']} | {score.CAPS['condition']}개 |
| 내야 하는 서류 | 1개당 +{W['document']} (글에 없으면 0) | {score.CAPS['document']}개 |
| 안내문 난이도(문장 길이·어려운 말) | 0~3점 × {W['readability']} | 3점 |

100점 = 모든 항목이 상한일 때의 합. 가중치는 팀 결정 전 안이며, 미국의 복지 신청 부담 평가 기준을 참고해 조정합니다.

**쉬운 설명 검사**: 원문 숫자(나이·%·금액·기간·횟수)를 뽑아 쉬운 설명에도 있는지 대조합니다. 
`200만원`과 `2,000,000원`은 같은 값으로 봅니다. 하나라도 빠지면 한 번 다시 만들고, 그래도 빠지면 '사람 확인 필요'를 붙입니다.

**자료**: 한국사회보장정보원 복지서비스 API(중앙부처 15090532, 지자체 15108347) 샘플 50건. 
구비서류 전용 칸이 없어 신청방법 글에 있는 서류만 씁니다.
""")
