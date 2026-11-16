"""정답표 입력 화면. 원문을 읽고 질문에 답하면 eval/gold_<이름>.csv가 채워진다.

실행: .venv/bin/python -m streamlit run eval/label_app.py
AI 결과는 보여 주지 않는다(정답이 AI 쪽으로 기울지 않게).
"""
import csv
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from parse import load_all  # noqa: E402

PEOPLE = {"최재용": "gold_jaeyong.csv", "박다윗": "gold_dawit.csv", "김도형": "gold_dohyung.csv"}
DRAFT, REVIEWED = "AI 초안(검수 전)", "AI 초안·사람 검수"
COLS = ["servId", "servNm", "online", "visit_required", "n_agencies", "n_conditions", "n_documents",
        "documents(쉼표로)", "메모", "원문"]

st.set_page_config(page_title="정답표 입력", page_icon="📝", layout="wide")
st.markdown("<style>.src{font-size:1.05rem;line-height:1.8;white-space:pre-wrap}</style>", unsafe_allow_html=True)


@st.cache_data
def services():
    return {s["servId"]: s for s in load_all()}


def read(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


SV = services()
who = st.sidebar.radio("누구 정답표인가요?", list(PEOPLE))
path = ROOT / "eval" / PEOPLE[who]
rows = read(path)
draft = [DRAFT in r.get("메모", "") for r in rows]
done = [bool(r["online"].strip()) and not d for r, d in zip(rows, draft)]
st.sidebar.progress(sum(done) / len(rows), text=f"{sum(done)}/{len(rows)}건 완료" + (f" · 검수 남음 {sum(draft)}건" if any(draft) else ""))
labels = [f"{'📝' if dr else '✅' if d else '⬜'} {i + 1}. {r['servNm']}" for i, (r, d, dr) in enumerate(zip(rows, done, draft))]
first_todo = next((i for i, d in enumerate(done) if not d), 0)  # 빈칸이거나 검수 전인 첫 서비스
if "_goto" in st.session_state:  # 저장 후 다음 서비스로 (위젯 만들기 전에 바꿔야 함)
    st.session_state[f"idx_{who}"] = st.session_state.pop("_goto")
if "_saved" in st.session_state:
    st.toast(st.session_state.pop("_saved"))
key = f"idx_{who}"
if key not in st.session_state:  # 처음 열 때는 아직 안 채운 첫 서비스부터
    st.session_state[key] = first_todo
idx = st.sidebar.radio("서비스", range(len(rows)), format_func=lambda i: labels[i], key=key)
st.sidebar.caption("원문에 적힌 것만 보고 답해 주세요. 복지로·검색으로 따로 찾지 않아요(원문에 없으면 '아니오'). AI 결과는 일부러 보여 주지 않아요. 기준: eval/README.md")

row = rows[idx]
s = SV[row["servId"]]
st.title(f"{idx + 1}. {s['servNm']}")
st.caption(f"{s['src']} · {s['region']} · {s['org']}")
if draft[idx]:
    st.info("📝 AI가 채운 초안입니다. 원문과 비교해 틀린 곳만 고치고 '저장하고 다음으로'를 누르세요. 맞으면 그대로 저장하면 됩니다.")

left, right = st.columns([3, 2])
with left:
    for key, title in (("target", "지원대상"), ("criteria", "선정기준"), ("apply", "신청방법")):
        st.markdown(f"#### {title}")
        text = (s["apply_method_name"] + "\n" if key == "apply" and s["apply_method_name"] else "") + (s[key] or "(비어 있음)")
        st.markdown(f"<div class='src'>{text}</div>", unsafe_allow_html=True)
    with st.expander("지원내용 (조건이 아니라 받는 것 — 참고용)"):
        st.markdown(f"<div class='src'>{s['benefit']}</div>", unsafe_allow_html=True)

# 이전에 적은 값 불러오기
memo = row.get("메모", "")
prev_cond = memo.split("조건: ", 1)[1].split(" || ", 1)[0].replace(" | ", "\n") if "조건: " in memo else ""
prev_note = memo.split("메모: ", 1)[1] if "메모: " in memo else ""
prev_note = prev_note.replace(DRAFT + " / ", "").replace(DRAFT, "").replace(REVIEWED + " / ", "").replace(REVIEWED, "")
was_ai = DRAFT in memo or REVIEWED in memo
yn = lambda v, d=None: {"Y": "예", "N": "아니오"}.get(v.strip().upper(), d)

with right:
    with st.form(f"f_{who}_{idx}"):
        st.markdown("##### 1. 인터넷(복지로·정부24·홈페이지)으로 신청할 수 있나요?")
        online = st.radio("online", ["예", "아니오"], index=None if not yn(row["online"]) else ["예", "아니오"].index(yn(row["online"])),
                          horizontal=True, label_visibility="collapsed")
        st.markdown("##### 2. 직접 가는 것 말고 다른 신청 방법(인터넷·우편·전화·이메일·팩스)이 있나요?")
        other = st.radio("other", ["있다", "없다(꼭 방문)"], horizontal=True, label_visibility="collapsed",
                         index=None if not yn(row["visit_required"]) else (1 if yn(row["visit_required"]) == "예" else 0))
        st.markdown("##### 3. 원문에 나온 단계를 모두 고르세요")
        n_ag = int(row["n_agencies"]) if row["n_agencies"].strip() else 0
        a1 = st.checkbox("신청받는 곳 (예: 주민센터에 신청)", value=n_ag >= 1)
        a2 = st.checkbox("조사·심사하는 곳 (예: 구청에서 서류 검토)", value=n_ag >= 2)
        a3 = st.checkbox("결정·선정하는 곳 (예: 시군구에서 결정·통보)", value=n_ag >= 3)
        st.markdown("##### 4. 받을 조건을 한 줄에 하나씩 적으세요")
        st.caption("같은 말 반복·'A 또는 B'·나이 범위는 1개. 금액·제외 항목·신청 기간·우선순위는 조건 아님")
        cond = st.text_area("cond", prev_cond, height=140, placeholder="(예시) 만 65세 이상\n(예시) 기준중위소득 40% 이내",
                            label_visibility="collapsed")
        st.markdown("##### 5. 내야 하는 서류를 한 줄에 하나씩 적으세요")
        st.caption("원문에 서류 이야기가 전혀 없으면 비워 두세요(= 서류 정보 없음)")
        docs = st.text_area("docs", row["documents(쉼표로)"].replace(", ", "\n"), height=100,
                            placeholder="(예시) 신청서\n(예시) 통장사본", label_visibility="collapsed")
        note = st.text_input("애매했던 점 (선택)", prev_note)
        saved = st.form_submit_button("저장하고 다음으로", type="primary", width="stretch")

    if saved:
        if online is None or other is None:
            st.error("1번과 2번에 답해 주세요.")
        else:
            conds = [c.strip() for c in cond.splitlines() if c.strip()]
            doc_list = [d.strip() for d in docs.splitlines() if d.strip()]
            row.update({
                "online": "Y" if online == "예" else "N",
                "visit_required": "Y" if other.startswith("없다") else "N",
                "n_agencies": str(a1 + a2 + a3),
                "n_conditions": str(len(conds)),
                "n_documents": str(len(doc_list)) if doc_list else "",
                "documents(쉼표로)": ", ".join(doc_list),
                "메모": "조건: " + " | ".join(conds) + (" || 메모: " + " / ".join(x for x in [REVIEWED if was_ai else "", note.strip()] if x) if (was_ai or note.strip()) else ""),
            })
            write(path, rows)
            nxt = next((i for i in range(idx + 1, len(rows)) if not rows[i]["online"].strip() or DRAFT in rows[i].get("메모", "")), None)
            if nxt is not None:
                st.session_state["_goto"] = nxt
            st.session_state["_saved"] = f"저장했어요: {s['servNm']} (조건 {len(conds)}개, 서류 {len(doc_list) or '정보 없음'})"
            st.rerun()
