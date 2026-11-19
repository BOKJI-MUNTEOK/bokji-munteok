"""공식 복지 API 요청과 DB 기반 화면 데이터. 키·요청 URL은 출력하지 않는다."""
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from env import load_env
from parse import merge_detail, parse_list_item
from welfare_store import WelfareStore

ENDPOINTS = {
    "central": ("NationalWelfareInformationsV001", "NationalWelfarelistV001", "NationalWelfaredetailedV001"),
    "local": ("LocalGovernmentWelfareInformations", "LcgvWelfarelist", "LcgvWelfaredetailed"),
}
LABELS = {"central": "중앙부처", "local": "지자체"}


class APIError(RuntimeError):
    pass


class QuotaExceeded(APIError):
    pass


def api_keys():
    load_env()
    values = [urllib.parse.unquote(os.environ.get(name, "")).strip()
              for name in ("DATA_GO_KR_KEY", "DATA_GO_KR_KEY_2", "DATA_GO_KR_KEY_3")]
    return list(dict.fromkeys(key for key in values if key))


class RequestGate:
    """하나의 갱신 작업이 초당 요청을 몰아서 보내지 않도록 간격을 둔다."""
    def __init__(self):
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self):
        with self.lock:
            time.sleep(max(0, .2 - (time.monotonic() - self.last)))
            self.last = time.monotonic()


class DataClient:
    def __init__(self, store, keys, gate=None, owner=None):
        self.store, self.keys, self.gate = store, keys, gate or RequestGate()
        self.owner = owner

    def request(self, source, detail=False, **params):
        if not self.keys:
            raise APIError("공공데이터 인증 키가 설정되지 않았어요.")
        base, listing, detailed = ENDPOINTS[source]
        if source == "central":
            params["callTp"] = "D" if detail else "L"
            if not detail:
                params["srchKeyCode"] = "003"
        label = LABELS[source]
        attempts = 0
        for _ in range(len(self.keys) + 3):
            self.gate.wait()
            if self.owner:
                self.store.renew(self.owner)
            reserved = self.store.reserve_key(source, self.keys)
            if reserved is None:
                raise QuotaExceeded(f"{label}의 오늘 사용 가능한 호출량을 모두 사용했습니다. 다음날 예약 갱신에서 이어서 수집합니다.")
            key, day = reserved
            query = urllib.parse.urlencode({"serviceKey": key, **params})
            url = f"https://apis.data.go.kr/B554287/{base}/{detailed if detail else listing}?{query}"
            attempts += 1
            try:
                with urllib.request.urlopen(url, timeout=15) as response:
                    data = response.read()
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()
                if status in (401, 403):
                    self.store.block_key(source, key, day)
                    continue
                if status == 429 or status >= 500:
                    if attempts < 3:
                        time.sleep(attempts)
                        continue
                raise APIError(f"{label} API 연결 오류(HTTP {status}). 저장된 자료는 유지합니다.") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempts < 3:
                    time.sleep(attempts)
                    continue
                raise APIError(f"{label} API에 연결하지 못했습니다. 잠시 후 자동으로 다시 시도합니다.") from None
            try:
                root = ET.fromstring(data)
            except ET.ParseError:
                raise APIError(f"{label} API 응답을 읽지 못했습니다.") from None
            code = root.findtext(".//returnReasonCode") or root.findtext(".//resultCode")
            portal_error = root.findtext(".//errMsg") or root.findtext(".//returnAuthMsg")
            if code in ("20", "21", "22", "30", "31", "32"):
                self.store.block_key(source, key, day)
                continue
            if code == "23" and attempts < 3:
                time.sleep(attempts * 2)
                continue
            if portal_error or code not in ("0", "00"):
                safe_code = code if code and re.fullmatch(r"[A-Za-z0-9_]{1,30}", code) else "UNKNOWN"
                raise APIError(f"{label} API 오류({safe_code}). 잠시 후 자동으로 다시 확인합니다.")
            return root
        raise APIError(f"{label} API의 인증 또는 호출 한도를 확인해 주세요.")

    def catalog(self, source):
        rows, total = {}, None
        for page in range(1, 1001):
            root = self.request(source, pageNo=page, numOfRows=500)
            try:
                count = int(root.findtext("totalCount"))
                if count < 0 or int(root.findtext("pageNo")) != page or (total is not None and total != count):
                    raise ValueError
            except (ValueError, TypeError):
                raise APIError(f"{LABELS[source]} 목록이 변경되었거나 페이지 정보가 올바르지 않습니다.") from None
            total, previous = count, len(rows)
            for element in root.iter("servList"):
                item = parse_list_item(element, source)
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item["servId"]) or not item["servNm"]:
                    raise APIError(f"{LABELS[source]} 목록에 서비스 식별 정보가 없습니다.")
                rows[item["servId"]] = item
            if len(rows) == total:
                return list(rows.values())
            if len(rows) <= previous or len(rows) > total:
                break
        raise APIError(f"{LABELS[source]} 전체 목록을 받지 못했습니다. 저장된 목록을 유지합니다.")

    def detail(self, item):
        root = self.request(item["api_source"], detail=True, servId=item["servId"])
        if root.findtext("servId") != item["servId"] or not root.findtext("servNm"):
            raise APIError("요청한 서비스와 상세 응답이 일치하지 않습니다.")
        return merge_detail(item, root)


class WelfareAPI:
    """화면과 명령줄에서 저장 자료만 읽는다. API 수집은 예약 작업이 맡는다."""
    def __init__(self, database_url=None):
        load_env()
        self.database_url = database_url or os.environ.get("MONGODB_URI", "")
        self.snapshot_lock = threading.Lock()
        self.snapshot_cache = None

    def catalog(self):
        with self.snapshot_lock, WelfareStore(self.database_url) as store:
            revision = store.revision()
            if self.snapshot_cache is None or self.snapshot_cache["sync"]["revision"] != revision:
                self.snapshot_cache = store.snapshot()
            return self.snapshot_cache

    def services(self, catalog):
        return catalog["services"]

    def detail(self, item):
        with WelfareStore(self.database_url) as store:
            service = store.service(item["api_source"], item["servId"])
        if service is None:
            raise APIError("공식 목록에서 삭제된 서비스입니다.")
        if not service.get("detail_loaded"):
            raise APIError("이 서비스의 상세는 자동 수집 대기 중입니다. 매일 새벽 4시(한국시간) 예약 갱신에서 수집하며 저장 후 신청 안내와 점수가 표시됩니다.")
        return service
