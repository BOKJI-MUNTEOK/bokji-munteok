"""서류를 한 장씩 열고 작성 결과를 확인하는 시민용 화면.

개인 입력과 생성된 문서는 현재 Streamlit 세션에만 둔다. 공유 캐시나 DB에 넣지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import re

import streamlit as st

import address_check
import document_workspace as workspace
import doc_assist_ai
import document_viewer
import field_format
import fill_form
import packet
import paperwork


MIMES = {"pdf": "application/pdf", "hwp": "application/x-hwp", "hwpx": "application/hwp+zip"}
# 서류 발급 안내의 온라인 발급처 바로가기
ONLINE_SITES = [("정부24", "https://www.gov.kr"), ("홈택스", "https://www.hometax.go.kr"),
                ("전자가족관계등록시스템", "https://efamily.scourt.go.kr"), ("복지로", "https://www.bokjiro.go.kr")]

# 해외 서버에서 도로명주소 API가 막혔을 때 사용자 브라우저에서 같은 API를 불러 응답을 돌려준다.
# 검색어를 차례로 넣어 보고, 결과가 있거나 마지막 검색어까지 해 본 응답을 한 번만 보낸다.
JUSO_BROWSER_JS = """
export default function ({ data, setStateValue }) {
  if (!data || !Array.isArray(data.keywords)) return;
  const tag = JSON.stringify(data.keywords);
  if (window.__bokjiJuso === tag) return;
  window.__bokjiJuso = tag;
  (async () => {
    let reply = { keyword: data.keywords[0] || "", data: null, error: "" };
    for (const keyword of data.keywords) {
      const q = new URLSearchParams({ confmKey: data.key, currentPage: "1", countPerPage: String(data.count),
                                      keyword, resultType: "json" });
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 8000);
      try {
        const r = await fetch(data.url + "?" + q.toString(), { signal: ctl.signal, credentials: "omit" });
        const body = await r.json();
        reply = { keyword, data: body, error: "" };
        if (((body.results || {}).juso || []).length) break;
      } catch (e) {
        reply = { keyword, data: null, error: String((e && e.name) || "Error") };
        break;
      } finally {
        clearTimeout(timer);
      }
    }
    window.__bokjiJuso = "";
    setStateValue("reply", reply);
  })();
}
"""
_juso_browser = st.components.v2.component("bokji_juso_browser", js=JUSO_BROWSER_JS)


def _drop_output(state: dict) -> None:
    state.pop("prepared", None)
    state.pop("prepared_for", None)


def _phone_note(container, value: str) -> None:
    if value.strip():
        if field_format.phone_parts(value) is None:
            container.warning("전화번호 자릿수를 확인해 주세요. 예: 010-1234-5678")
        else:
            container.caption(f"서류에는 {field_format.format_phone(value)}처럼 넣습니다. 서류에 따라 표기가 바뀔 수 있어요.")


def _address_box(sid: str, address: str) -> None:
    """주소 확인: 사용자가 누를 때만 주소 한 줄을 AI 검수·도로명주소 검색으로 보낸다."""
    result_key = f"dw_addrcheck_{sid}"
    if st.button("주소 확인하기", key=f"dw_addrbtn_{sid}", disabled=not address.strip(), width="stretch",
                 help="띄어쓰기를 바로잡고 도로명주소에서 실제 주소인지 찾아봅니다. 주소만 보내고 이름·연락처는 보내지 않습니다."):
        with st.spinner("주소를 확인하고 있어요."):
            try:
                st.session_state[result_key] = address_check.check(address)
            except Exception:  # 개인정보 보호: 주소·예외 상세를 화면에 남기지 않는다
                st.session_state.pop(result_key, None)
                st.error("주소를 확인하지 못했습니다. 입력한 주소는 그대로 두었으니 직접 확인해 주세요.")
    checked = st.session_state.get(result_key)
    if not checked:
        return
    if checked["original"] != re.sub(r"[\s\u3000]+", " ", address).strip():
        st.session_state.pop(result_key, None)  # 확인 뒤 주소를 고쳤다면 이전 결과는 맞지 않는다.
        return
    if checked.get("browser_search"):
        checked = _browser_search(sid, result_key, checked)
        if checked.get("browser_search"):
            return
    options = list(dict.fromkeys([c["full"] for c in checked["candidates"]] or [checked["suggestion"]]))
    if checked["verified"]:
        st.success("도로명주소에서 찾은 주소입니다. 맞는 주소를 골라 주세요." if len(options) > 1 else "도로명주소에서 확인한 주소입니다.")
    for note in checked["notes"]:
        st.caption(note)
    if checked["candidates"] and not checked["verified"]:
        options = list(dict.fromkeys([checked["suggestion"]] + options))  # 비슷한 주소만 찾았으면 입력한 주소도 고를 수 있게 둔다.
    if options == [checked["original"]]:
        st.caption("고칠 곳이 없습니다.")
        return
    st.radio("바꿀 주소", options, key=f"dw_addrpick_{sid}", label_visibility="collapsed")
    # 서류 화면은 대화상자 안에 있다. st.rerun()은 대화상자를 닫으므로 클릭 콜백에서 값을 바꾼다.
    st.button("이 주소로 바꾸기", key=f"dw_addrapply_{sid}", width="stretch", on_click=_apply_address, args=(sid,))


def _browser_search(sid: str, result_key: str, checked: dict) -> dict:
    """서버가 도로명주소 API에 연결하지 못했으면 브라우저에서 찾고, 응답이 오면 결과를 마저 채운다."""
    request = address_check.browser_request(checked)
    if request is None:
        done = address_check.apply_browser_result(checked, None)
        st.session_state[result_key] = done
        return done
    # 같은 주소를 다시 확인할 때도 새로 찾도록 확인할 때마다 키를 바꾼다.
    turn = st.session_state.setdefault(f"dw_addrturn_{sid}", 0)
    reply = _juso_browser(key=f"dw_addrjuso_{sid}_{turn}", data=request,
                          on_reply_change=lambda: None).get("reply")
    if not reply:
        st.caption("도로명주소에서 찾고 있어요.")
        return checked
    st.session_state[f"dw_addrturn_{sid}"] = turn + 1
    done = address_check.apply_browser_result(checked, reply)
    st.session_state[result_key] = done
    return done


def _apply_address(sid: str) -> None:
    st.session_state[f"dw_address_{sid}"] = st.session_state.get(f"dw_addrpick_{sid}", "")
    st.session_state.pop(f"dw_addrcheck_{sid}", None)


def _clear_inputs(sid: str, state_key: str) -> None:
    for key in list(st.session_state):
        if key == state_key or (key.startswith("dw_") and key.endswith(f"_{sid}")):
            del st.session_state[key]


def _input_profile(sid: str, me: dict | None, *, has_result: bool) -> dict[str, str]:
    me = me or {}
    address_key = f"dw_address_{sid}"
    if address_key not in st.session_state:
        # AI 상담에서 전체 주소를 들었으면 그것을, 아니면 지역(시·도·시·군·구)부터 채워 둔다.
        st.session_state[address_key] = (str(me.get("address") or "").strip()
                                         or " ".join(str(me.get(k) or "").strip() for k in ("sido", "sgg")).strip())
    st.session_state.setdefault(f"dw_name_{sid}", str(me.get("name") or ""))
    st.session_state.setdefault(f"dw_phone_{sid}", str(me.get("phone") or ""))
    st.session_state.setdefault(f"dw_birth_{sid}", str(me.get("birth") or ""))
    with st.expander("내 정보 입력·수정", expanded=not has_result):
        st.write("서류에 적을 정보를 입력해 주세요. 모르는 항목은 비워 두셔도 됩니다.")
        a, b = st.columns(2)
        name = a.text_input("이름", key=f"dw_name_{sid}")
        phone = b.text_input("연락처", key=f"dw_phone_{sid}", placeholder="010-0000-0000")
        _phone_note(b, phone)
        a, b = st.columns(2)
        birth = a.text_input("생년월일", key=f"dw_birth_{sid}", placeholder="1950-01-01")
        if birth.strip():
            if (parsed := field_format.parse_date(birth)) is None:
                a.warning("생년월일을 1950-01-01 또는 19500101처럼 입력해 주세요.")
            else:
                a.caption(f"{parsed.year}년 {parsed.month}월 {parsed.day}일로 읽었습니다. 서류의 날짜 표기에 맞춰 넣습니다.")
        address = st.text_input("주소", key=address_key, placeholder="시·군·구부터 상세 주소까지")
        _address_box(sid, address)
        with st.expander("계좌 정보가 필요한 서류라면"):
            bank = st.text_input("은행명", key=f"dw_bank_{sid}")
            account = st.text_input("계좌번호", key=f"dw_account_{sid}")
            holder = st.text_input("예금주", key=f"dw_holder_{sid}")
        with st.expander("대신 신청하는 분이 있다면"):
            proxy = st.text_input("대신 신청하는 분의 이름", key=f"dw_proxy_{sid}")
            relation = st.text_input("신청인과의 관계", key=f"dw_relation_{sid}", placeholder="예: 자녀")
            proxy_phone = st.text_input("대신 신청하는 분의 연락처", key=f"dw_proxy_phone_{sid}")
            _phone_note(st, proxy_phone)
        with st.expander("주민등록번호·소득이 필요한 서류라면"):
            st.write("이 항목이 필요한 서류에만 입력해 주세요.")
            rrn = st.text_input("주민등록번호", key=f"dw_rrn_{sid}", type="password")
            income = st.text_input("월 소득", key=f"dw_income_{sid}")
        st.caption("입력 정보는 서류 작성을 위해 서버에서 처리하며, 이 화면의 세션에만 보관합니다. "
                   "‘주소 확인하기’를 누를 때만 주소 한 줄을 AI 검수와 도로명주소 검색으로 보냅니다.")
    return fill_form.build_profile({"name": name}, {
        "phone": phone, "address": address, "birth_date": birth, "rrn": rrn,
        "bank": bank, "account_number": account, "account_holder": holder,
        "proxy": proxy, "relation": relation, "proxy_phone": proxy_phone,
        "monthly_income": income,
    }, today=datetime.now(timezone(timedelta(hours=9))).date())


def _site_links(where: str) -> list[tuple[str, str]]:
    return [(name, url) for name, url in ONLINE_SITES if name in where]


def _guide(row: dict, sid: str, state: dict, *, reason: str) -> None:
    """신청서가 없거나 자동 작성이 안 될 때: 어디서 어떻게 받는지 쉬운 말로 차례대로 안내한다."""
    svc = row["svc"]
    with st.container(border=True):
        st.markdown("#### 신청서는 이렇게 받으세요")
        st.write(reason)
        steps = []
        if svc.get("link"):
            steps.append("**온라인**: 복지로에서 이 복지를 찾아 ‘신청하기’가 있는지 보세요. 있으면 집에서 바로 신청할 수 있어요.")
        steps.append("**방문**: 사는 곳의 읍·면·동 행정복지센터(주민센터)"
                     + (f"나 {svc['org']}" if svc.get("org") else "") + "에 가면 신청서를 받아 그 자리에서 쓸 수 있어요. 신분증을 꼭 챙기세요.")
        if svc.get("contact"):
            steps.append(f"**전화**: {svc['contact']}에 전화해서 신청서를 문자·팩스·우편으로 받을 수 있는지, 가족이 대신 낼 수 있는지 물어보세요.")
        else:
            steps.append("**전화**: 주민센터에 전화해서 이 복지 신청서를 어디서 받는지 물어보세요.")
        for i, step in enumerate(steps, 1):
            st.markdown(f"{i}. {step}")
        if svc.get("link"):
            st.link_button("복지로에서 신청 방법 보기", svc["link"], width="stretch")
        st.caption("함께 낼 서류와 떼는 곳은 아래 ‘함께 준비할 서류와 떼는 곳’에서 확인하세요.")


@st.cache_data(ttl=7 * 24 * 3600, max_entries=3000, show_spinner=False)
def cached_guide(serv_id: str, source_hash: str, _svc: dict) -> dict:
    """공식 안내문으로 만든 서류·신청 안내. 공개 자료라 사람 사이에 함께 쓴다. 실패는 빈 dict."""
    return doc_assist_ai.easy_guide(_svc) or {}


def guide_source(svc: dict) -> str:
    """안내문이 바뀌면 서류 안내를 다시 만든다. 비어 있는 항목이 있어도 계산되게 get으로 모은다."""
    return workspace.fingerprint_inputs({k: svc.get(k) for k in (
        "servNm", "org", "target", "criteria", "benefit", "apply_method_name", "apply", "contact")})


def needs_ai_guide(row: dict) -> bool:
    try:
        return not paperwork.documents(row)[1]
    except Exception:
        return True


def render_needed_documents(row: dict) -> None:
    """함께 준비할 서류와 떼는 곳. 공식 목록이 없으면 AI가 안내문을 읽고 찾아 준다."""
    svc = row["svc"]
    sid = str(svc["servId"])
    try:
        docs, listed = paperwork.documents(row)
    except Exception:
        docs, listed = list(paperwork.BASIC), False
    guide = None
    if not listed and doc_assist_ai.available():
        with st.spinner("AI가 공식 안내문을 읽고 필요한 서류와 떼는 곳을 찾고 있어요"):
            try:
                guide = cached_guide(sid, guide_source(svc), svc) or None
            except Exception:
                guide = None
    if guide and guide.get("documents"):
        if not any(d["official"] for d in guide["documents"]):
            st.info("공식 안내에 서류 목록이 없어 AI가 이런 복지에 보통 필요한 서류를 정리했어요. 신청 전에 담당 기관에 꼭 확인하세요.")
        for i, d in enumerate(guide["documents"]):
            badge = "안내문에 있음" if d["official"] else "보통 필요 · 확인 필요"
            st.checkbox(f"{d['name']}  ({badge})", key=f"doc_{sid}_ai_{i}")
            st.caption(d["where"] + (" · 온라인 발급 가능" if d["online"] else ""))
            for name, url in _site_links(d["where"]):
                st.link_button(f"{name} 바로가기", url)
        if guide.get("steps"):
            st.markdown("**신청 순서**")
            for i, step in enumerate(guide["steps"], 1):
                st.markdown(f"{i}. {step}")
        if guide.get("tip"):
            st.info(guide["tip"])
        st.caption("공식 안내문을 AI가 쉽게 정리한 것입니다. 정확한 내용은 담당 기관에 확인하세요.")
        with st.expander("원문 보기 (복지로 공식 안내)"):
            for field, label in (("apply_method_name", "신청방법"), ("apply", "신청절차"), ("target", "지원대상"),
                                 ("criteria", "선정기준"), ("contact", "문의")):
                if svc.get(field):
                    st.markdown(f"**{label}**")
                    st.text(svc[field])
        return
    if not listed:
        st.info("공식 안내에 서류 목록이 없어요. 아래는 대부분의 복지 신청에 필요한 기본 서류예요. 담당 기관에 꼭 확인하세요.")
    for i, d in enumerate(docs):
        w = paperwork.where(d)
        st.checkbox(d, key=f"doc_{sid}_{i}")
        st.caption(f"{w[0]}{' · 온라인 발급 가능' if w[1] else ''}" if w else "어디서 떼는지 담당 기관에 물어보세요")
        for name, url in (_site_links(w[0]) if w else []):
            st.link_button(f"{name} 바로가기", url)


def _reference_list(refs: list[dict]) -> None:
    """사업 안내·지침·공고 같은 참고 자료. 신청서가 아니므로 내려받기만 제공한다."""
    if not refs:
        return
    with st.expander(f"참고 자료 {len(refs)}개 — 사업 안내·지침·공고 (신청서가 아니에요)"):
        st.caption("지원 대상·금액·절차를 자세히 알고 싶을 때 내려받아 보세요. 작성해서 내는 서류는 아닙니다.")
        for d in refs:
            c = st.columns([5, 2], vertical_alignment="center")
            c[0].markdown(f"📄 {d['name']}")
            c[1].link_button("내려받기", d["url"], width="stretch")


def _copy_card(profile: dict[str, str]) -> None:
    """자동 작성이 안 되는 서류에 손으로 옮겨 적을 내용."""
    rows = [("이름", profile.get("applicant.name")), ("연락처", field_format.format_phone(profile.get("applicant.phone", ""))),
            ("주소", profile.get("applicant.address"))]
    birth = field_format.parse_date(profile.get("applicant.birth_date", ""))
    if birth:
        rows.insert(1, ("생년월일", f"{field_format.format_date(birth, 'korean')} (서류에 ‘. .’ 칸이면 {field_format.format_date(birth, 'dot')})"))
    rows += [("은행·계좌", profile.get("account.full")), ("예금주", profile.get("account.holder"))]
    rows = [(k, v) for k, v in rows if v]
    if rows:
        with st.container(border=True):
            st.markdown("**옮겨 적을 내용**")
            for k, v in rows:
                st.markdown(f"- {k}: **{v}**")


def render_document_workspace(row: dict, me: dict | None = None) -> None:
    st.markdown("""<style>
    [class*="st-key-dw_shell_"] [data-testid="stWidgetLabel"] p,
    [class*="st-key-dw_shell_"] [data-testid="stMarkdownContainer"] p{font-size:1.1rem;line-height:1.65}
    [class*="st-key-dw_shell_"] button{min-height:48px}
    [class*="st-key-dw_shell_"] input{min-height:48px;font-size:1.1rem}
    [class*="st-key-dw_shell_"] button:focus-visible{outline:3px solid var(--bm-accent,#256EF4);outline-offset:3px}
    </style>""", unsafe_allow_html=True)
    with st.container(key=f"dw_shell_{row['svc']['servId']}"):
        _render_document_workspace(row, me)


def _render_document_workspace(row: dict, me: dict | None = None) -> None:
    svc = row["svc"]
    sid = str(svc["servId"])
    state_key = f"document_workspace_{sid}"
    state = st.session_state.setdefault(state_key, {})
    me_version = workspace.fingerprint_inputs(me or {})
    if state.get("me_version") not in (None, me_version):
        # A changed consultation profile must not leave an old name/address in the form.
        for key in list(st.session_state):
            if key.startswith("dw_") and key.endswith(f"_{sid}") and key != f"dw_choice_{sid}":
                del st.session_state[key]
        state.clear()
    state["me_version"] = me_version
    docs = workspace.list_documents(svc)
    forms = [d for d in docs if d["kind"] == "form"]
    refs = [d for d in docs if d["kind"] == "reference"]
    st.markdown("### 서류를 채우고 바로 확인하세요")
    st.write("서류를 한 장씩 열어 보고, 필요한 서류만 인쇄하거나 저장하세요.")
    if not forms:
        _guide(row, sid, state, reason="이 복지는 복지로에 올라온 신청서 파일이 없어요. 아래 방법으로 신청서를 받을 수 있어요."
               if not refs else "복지로에는 사업 안내·지침 같은 참고 자료만 올라와 있어요. 신청서는 아래 방법으로 받을 수 있어요.")
    _reference_list(refs)
    if not forms:
        return

    lookup = {doc["id"]: doc for doc in forms}
    st.markdown("**1. 서류 고르기**")
    choice = st.selectbox("열어 볼 서류", list(lookup), format_func=lambda key: lookup[key]["name"], key=f"dw_choice_{sid}")
    selected = lookup[choice]
    selection_version = workspace.fingerprint_inputs({"document": selected,
        "detail_version": svc.get("detail_fetched_at"), "profile": me or {}})
    if state.get("selection_version") != selection_version:
        state.clear()
        state["me_version"] = me_version
        state["selection_version"] = selection_version

    just_opened = False
    if st.button("이 서류 열기", key=f"dw_open_{sid}", type="primary", width="stretch"):
        _drop_output(state)
        state.pop("original", None)
        state.pop("analysis", None)
        with st.spinner("서류를 불러오고 작성할 곳을 확인하고 있어요."):
            # 복지로에는 같은 이름의 첨부가 여러 번 올라오고 그중 일부는 지워져 있다. 같은 이름의 다른 첨부도 시도한다.
            tries = [selected] + [d for d in docs if d["name"] == selected["name"] and d["id"] != selected["id"]]
            for doc in tries:
                try:
                    raw = packet.download(doc["url"])
                    initial = workspace.prepare_document(raw, selected["name"], {})
                    state.update(original=raw, analysis=initial)
                    just_opened = True
                    break
                except Exception:
                    continue
            else:
                st.error("서류를 불러오지 못했습니다. 복지로에서 파일이 지워졌을 수 있어요. "
                         "잠시 후 ‘이 서류 열기’를 다시 누르거나, 아래 ‘복지로에서 신청 안내 보기’로 확인해 주세요.")
    if "original" not in state:
        st.caption("선택한 서류만 불러옵니다. 파일을 미리 저장할 필요가 없습니다.")
        return

    initial = state["analysis"]
    candidate = initial.get("analysis", {}).get("fillable_candidate", False)
    can_fill = candidate and initial.get("analysis", {}).get("automatic_allowed", True)
    if initial.get("template_status") == "verified":
        st.info("화면 검토를 거친 양식입니다. 작성 후 내 정보와 빈칸을 다시 확인해 주세요.")
    elif initial.get("template_status") == "changed":
        st.warning("양식이 바뀌었습니다. 작성한 내용을 한 번 더 살펴봐 주세요.")
    elif candidate:
        st.info("처음 확인하는 양식입니다. 채워진 곳과 남은 빈칸을 꼭 살펴봐 주세요.")

    if can_fill:
        st.markdown("**2. 내 정보로 채우기**")
        profile = _input_profile(sid, me, has_result="prepared" in state)
        version = workspace.fingerprint_inputs({"selection": selection_version,
                    "source": initial.get("fingerprint"), "profile": profile})
        if "prepared" in state and state.get("prepared_for") != version:
            _drop_output(state)
            st.info("입력한 정보가 바뀌었습니다. 아래 버튼을 눌러 서류에 다시 반영해 주세요.")
        has_input = any(v for k, v in profile.items() if not k.startswith("apply_date"))
        # 버튼을 끄지 않는다. 입력 직후(엔터 없이) 바로 누르면 꺼진 버튼은 클릭이 무시되기 때문이다.
        clicked = st.button("입력한 내용으로 서류 채우기", key=f"dw_fill_{sid}", type="primary", width="stretch")
        if clicked and not has_input:
            st.warning("이름이나 연락처 같은 내 정보를 먼저 입력해 주세요.")
        # 상담 등으로 이미 정보가 있으면 서류를 여는 즉시 채워 작성된 서류를 보여 준다.
        if (clicked or just_opened) and has_input:
            with st.spinner("입력한 내용으로 서류를 채우고 확인하고 있어요."):
                try:
                    prepared = workspace.prepare_document(state["original"], selected["name"], profile)
                    state.update(prepared=prepared, prepared_for=version)
                except Exception:
                    _drop_output(state)
                    st.error("서류를 채우지 못했습니다. 원본을 확인하거나 잠시 후 다시 시도해 주세요.")

    prepared = state.get("prepared")
    shown = prepared or initial
    filled = prepared is not None and shown.get("status") == "filled"
    st.markdown("**3. 작성된 서류 보기**" if can_fill else "**서류 보기**")
    if can_fill and prepared is None:
        # 열자마자 서류 내용을 보여 주되, 아직 내 정보가 안 들어간 서류임을 분명히 알린다.
        st.info("아직 내 정보가 들어가지 않은 서류예요. 위에 이름·연락처 등을 넣고 ‘입력한 내용으로 서류 채우기’를 누르면 "
                "이 자리에서 내 정보가 들어간 서류로 바뀌어요.")
    if filled:
        st.success(f"{shown['filled_count']}곳을 채웠습니다. 아래 서류에서 내 정보가 맞는지 확인해 주세요.")
        labels = shown.get("filled_labels", [])
        if labels:
            st.write("채운 항목: " + " · ".join(dict.fromkeys(labels)))
        if shown.get("formats"):
            st.caption("이 서류의 표기에 맞춰 넣었습니다: " + " · ".join(field_format.describe(shown["formats"])))
        st.write("남은 빈칸은 직접 작성해 주세요. 서명·도장은 인쇄한 뒤 직접 해 주세요.")
    elif prepared is not None:
        st.warning("자동으로 채우지 못했습니다. 아래에는 원본 서류를 보여 드립니다.")
    else:
        st.write("내용을 먼저 살펴보세요. 자동 작성이 어려운 서류는 인쇄한 뒤 직접 적을 수 있습니다.")

    for warning in dict.fromkeys(shown.get("warnings", [])):
        st.warning(warning)
    manual = shown.get("manual_labels", [])
    if manual:
        st.write("직접 확인할 곳: " + " · ".join(dict.fromkeys(manual)))
    if shown.get("status") == "drm":
        # 보안으로 잠긴 파일은 내려받아도 열리지 않는다. 받는 방법만 안내한다.
        _guide(row, sid, state, reason="이 파일은 기관 보안으로 잠겨 있어요. 아래 방법으로 신청서를 받으세요.")
        return
    if shown.get("preview_allowed"):
        written = field_format.adapt_profile(profile, shown.get("formats") or {}) if filled else {}
        focus = [written.get(k, "") for k in ("applicant.name", "applicant.address", "applicant.phone")]
        document_viewer.render_document(shown["data"], shown["filename"], shown["format"],
            key=f"dw_view_{sid}_{shown.get('fingerprint', '')[:12]}_{state.get('prepared_for', 'original')[:12]}",
            focus_texts=[value for value in focus if value], pages=shown.get("preview_pages"))
    else:
        st.info("이 서류는 화면에서 미리 볼 수 없습니다. 원본 서류를 저장해서 확인해 주세요.")
    st.download_button("작성한 서류 저장하기" if filled else "원본 서류 저장하기",
        shown["data"], file_name=shown["filename"], mime=MIMES.get(shown.get("format"), "application/octet-stream"),
        key=f"dw_save_{sid}", width="stretch")
    if not can_fill or (prepared is not None and not filled):
        if not can_fill:
            st.markdown("**옮겨 적을 내 정보**")
            _copy_card(_input_profile(sid, me, has_result=False))
        else:
            _copy_card(profile)
        _guide(row, sid, state, reason="이 서류는 자동으로 채우지 못했어요. 인쇄해서 직접 쓰거나 아래 방법으로 신청서를 받으세요.")
    if filled:
        with st.expander("내용을 고치거나 원본이 필요하다면"):
            st.write("위의 ‘내 정보 입력·수정’에서 고친 다음 ‘입력한 내용으로 서류 채우기’를 다시 눌러 주세요.")
            st.download_button("원본 서류 저장하기", state["original"], file_name=initial["filename"],
                mime=MIMES.get(initial.get("format"), "application/octet-stream"), key=f"dw_original_{sid}")
    st.button("입력 정보와 작성한 서류 지우기", key=f"dw_clear_{sid}", width="stretch",
              on_click=_clear_inputs, args=(sid, state_key))
