"""복지로 공식 API: 전체 목록 페이지 조회, 필요할 때 상세 조회, 서버 캐시.

인증 키와 요청 URL은 로그·캐시에 남기지 않는다. 실패 시 샘플로 대체하지 않는다.
"""
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from env import load_env
from parse import merge_detail, parse_list_item

ENDPOINTS = {
    "central": ("NationalWelfareInformationsV001", "NationalWelfarelistV001", "NationalWelfaredetailedV001"),
    "local": ("LocalGovernmentWelfareInformations", "LcgvWelfarelist", "LcgvWelfaredetailed"),
}
LABELS = {"central": "중앙부처", "local": "지자체"}
LIST_TTL = 6 * 60 * 60
DETAIL_TTL = 24 * 60 * 60
STALE_TTL = 7 * 24 * 60 * 60


class APIError(RuntimeError):
    """키와 요청 주소를 포함하지 않는 사용자용 오류."""


class WelfareAPI:
    def __init__(self, key=None, cache_dir=None):
        load_env()
        self.key = urllib.parse.unquote(key if key is not None else os.environ.get("DATA_GO_KR_KEY", "")).strip()
        self.cache_dir = Path(cache_dir or Path(tempfile.gettempdir()) / "bokji-welfare-api-v1")
        self.lock = threading.RLock()
        self.cooldown = {}  # 인증·호출량 오류 때 반복 클릭으로 API를 계속 부르지 않는다.

    def _request(self, source, detail=False, **params):
        if not self.key:
            raise APIError("복지 데이터 인증 키가 설정되지 않았어요. 운영자는 DATA_GO_KR_KEY 설정을 확인해 주세요.")
        if source in self.cooldown:
            until, message = self.cooldown[source]
            if time.time() < until:
                raise APIError(message)
        base, listing, detailed = ENDPOINTS[source]
        if source == "central":
            params["callTp"] = "D" if detail else "L"
            if not detail:
                params["srchKeyCode"] = "003"
        query = urllib.parse.urlencode({"serviceKey": self.key, **params})
        url = f"https://apis.data.go.kr/B554287/{base}/{detailed if detail else listing}?{query}"
        label = LABELS[source]
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=15) as response:
                    data = response.read()
            except urllib.error.HTTPError as exc:
                exc.close()
                if exc.code in (401, 403, 429):
                    message = f"{label} API 인증 또는 호출 한도를 확인해 주세요. 잠시 후 다시 시도할 수 있어요."
                    self.cooldown[source] = (time.time() + 300, message)
                    raise APIError(message) from None
                if exc.code < 500:
                    raise APIError(f"{label} API가 요청을 처리하지 못했어요 (HTTP {exc.code}).") from None
                data = None
            except (urllib.error.URLError, TimeoutError, OSError):
                data = None
            if data is not None:
                break
            if attempt == 2:
                raise APIError(f"{label} API에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.") from None
            time.sleep(0.5 * (attempt + 1))
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            raise APIError(f"{label} API 응답을 읽지 못했어요. 잠시 후 다시 시도해 주세요.") from None
        code = root.findtext(".//returnReasonCode") or root.findtext(".//resultCode")
        portal_error = root.findtext(".//errMsg") or root.findtext(".//returnAuthMsg")
        if portal_error or code not in ("0", "00"):
            # API의 원문 오류에는 요청 URL/인증 키가 포함될 수 있어 그대로 출력하지 않는다.
            safe_code = code if code and re.fullmatch(r"[A-Za-z0-9_]{1,30}", code) else "UNKNOWN"
            message = f"{label} API 오류({safe_code}). 인증·활용 신청 상태와 호출 한도를 확인해 주세요."
            self.cooldown[source] = (time.time() + 300, message)
            raise APIError(message)
        return root

    def _read(self, name, max_age):
        try:
            value = json.loads((self.cache_dir / f"{name}.json").read_text(encoding="utf-8"))
            expected = list if name.startswith("catalog-") else dict
            if not isinstance(value, dict) or not isinstance(value.get("data"), expected):
                return None
            if 0 <= time.time() - value["fetched_at"] < max_age:
                return value
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def _write(self, name, data):
        value = {"fetched_at": time.time(), "data": data}
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_dir, delete=False) as f:
                json.dump(value, f, ensure_ascii=False)
                temp_path = Path(f.name)
            try:
                os.replace(temp_path, self.cache_dir / f"{name}.json")
            finally:
                temp_path.unlink(missing_ok=True)
        except OSError:
            # 읽기 전용 환경이어도 현재 요청의 정상 응답은 쓸 수 있다.
            pass
        return value

    def _catalog_source(self, source):
        rows = {}
        total = None
        for page in range(1, 1001):
            root = self._request(source, pageNo=page, numOfRows=500)
            try:
                count = int(root.findtext("totalCount"))
                returned_page = int(root.findtext("pageNo"))
                if count < 0 or returned_page != page or (total is not None and total != count):
                    raise ValueError
            except (ValueError, TypeError):
                raise APIError(f"{LABELS[source]} 목록이 변경되었거나 페이지 정보가 올바르지 않아요. 다시 시도해 주세요.") from None
            total = count
            previous = len(rows)
            for element in root.iter("servList"):
                item = parse_list_item(element, source)
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item["servId"]) or not item["servNm"]:
                    raise APIError(f"{LABELS[source]} 목록에 서비스 식별 정보가 없어요.")
                rows[item["servId"]] = item
            if len(rows) == total:
                return list(rows.values())
            if len(rows) <= previous or len(rows) > total:
                break
        raise APIError(f"{LABELS[source]} 전체 목록을 받지 못했어요. 일부 목록으로 대체하지 않고 다시 시도합니다.")

    def catalog(self):
        """양쪽 API의 모든 페이지. 오류 때는 7일 이내 마지막 성공본과 경고를 돌려준다."""
        with self.lock:
            sources, warnings = {}, []
            for source in ENDPOINTS:
                name = f"catalog-{source}"
                cached = self._read(name, LIST_TTL)
                if cached is None:
                    try:
                        cached = self._write(name, self._catalog_source(source))
                    except APIError as exc:
                        cached = self._read(name, STALE_TTL)
                        if cached is None:
                            raise
                        warnings.append(f"{LABELS[source]} 목록 갱신 실패: 마지막 조회 자료를 표시합니다. {exc}")
                sources[source] = cached
            return {"services": [s for cached in sources.values() for s in cached["data"]],
                    "updated_at": min(c["fetched_at"] for c in sources.values()),
                    "counts": {source: len(c["data"]) for source, c in sources.items()},
                    "warnings": warnings}

    def _detail_name(self, item):
        # API ID를 경로로 직접 쓰지 않아 외부 응답에 경로 문자가 있어도 안전하다.
        digest = hashlib.sha256(f"{item['api_source']}:{item['servId']}".encode()).hexdigest()
        return f"detail-{digest}"

    def cached_detail(self, item, max_age=DETAIL_TTL):
        cached = self._read(self._detail_name(item), max_age)
        if cached and cached["data"].get("last_modified") == item.get("last_modified"):
            return {**cached["data"], **{k: item[k] for k in (
                "life", "groups", "themes", "region", "sido", "sgg", "link", "online_flag")},
                "detail_fetched_at": cached["fetched_at"]}
        return None

    def detail(self, item):
        with self.lock:
            cached = self.cached_detail(item)
            if cached:
                return cached
            try:
                root = self._request(item["api_source"], detail=True, servId=item["servId"])
                if root.findtext("servId") != item["servId"] or not root.findtext("servNm"):
                    raise APIError("요청한 서비스와 상세 응답이 일치하지 않아요. 잠시 후 다시 시도해 주세요.")
                svc = merge_detail(item, root)
                saved = self._write(self._detail_name(item), svc)
                return {**svc, "detail_fetched_at": saved["fetched_at"]}
            except APIError as exc:
                cached = self.cached_detail(item, STALE_TTL)
                if cached:
                    return {**cached, "detail_warning": f"상세 정보 갱신 실패: 마지막 조회 자료를 표시합니다. {exc}"}
                raise

    def services(self, catalog):
        """이미 조회한 상세만 합친다. 모든 서비스의 상세를 무작정 호출하지 않는다."""
        return [self.cached_detail(item) or item for item in catalog["services"]]
