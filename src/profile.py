"""대학 프로필 — 대학별 상수의 단일 진본 (2026-08-27, 표준화 2단계).

거점국립대 확산을 위해 "대학 1개 = 프로필 1개" 구조로 간다. 이 모듈은 코드가
읽던 대학 고유 상수(서비스 정체성·허용 호스트·URL 식별 파라미터·명칭 접두·
계층 임계값)를 JSON 한 곳으로 모은다.

**회귀 계약 (표준화 사이클 최대 수확):**
> 프로필화는 값의 정규화가 아니라, 기존 JNU 값에 대한 설정 주입이어야 한다.

즉 `profiles/jnu.json`은 지금 코드에 박혀 있던 값을 **그대로** 옮겨 적은 것이고,
전남대 서비스의 관측 가능한 동작은 한 글자도 달라지지 않아야 한다. record_id·
편제·검색 결과가 흔들리면 이미 발급된 인용이 깨지므로 §절대 금지 위반이다.
게이트는 record_id 전수 해시 동일성 + 기존 테스트 전건 통과다.

프로필 선택은 `RULE_COMPASS_PROFILE` 환경변수(기본 `jnu`). 파일이 없으면
**조용히 기본값으로 흐르지 않고 즉시 실패한다** — 빈 값이 stopword·호스트
검증에 스며들면 틀린 결과를 자신 있게 내놓게 된다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = _ROOT / "profiles"
DEFAULT_PROFILE_ID = "jnu"

_REQUIRED_TOP = ("univ_id", "univ_name", "name_prefixes", "service", "sources", "tier_rule")
_REQUIRED_SERVICE = ("display_name", "prompt_identity", "portal_tagline")
_REQUIRED_SOURCE = ("id", "host", "key_param")

# v3 신설 (2026-08-28). 소스와 계층이 직교한다는 실측에서 나왔다 — 한 소스가 여러
# 계층을 내고, 두 소스가 서로를 포함하지 않는 대학이 있다. `sources`를 배열로 바꾸고
# 소스마다 authority(정본/미러)를 붙인다. 전남대는 두 소스가 서로 다른 계층만
# 담당해 겹치지 않으므로 둘 다 canonical이고, 관측 동작은 v2와 동일해야 한다.
CANONICAL, MIRROR = "canonical", "mirror"


class ProfileError(RuntimeError):
    """프로필이 없거나 필수 항목이 빠졌다."""


class Profile:
    """읽기 전용 대학 프로필. 누락 키는 조용히 넘기지 않고 예외를 낸다."""

    def __init__(self, data: dict, path: Path | None = None) -> None:
        missing = [key for key in _REQUIRED_TOP if not data.get(key)]
        if missing:
            raise ProfileError(f"프로필 필수 항목 누락 {missing} ({path})")
        service = data["service"]
        missing = [key for key in _REQUIRED_SERVICE if not service.get(key)]
        if missing:
            raise ProfileError(f"프로필 service 필수 항목 누락 {missing} ({path})")
        sources = data["sources"]
        if not isinstance(sources, list):
            raise ProfileError(f"sources는 v3에서 배열이다 ({path})")
        self._by_id: dict[str, dict] = {}
        for entry in sources:
            missing = [key for key in _REQUIRED_SOURCE if not entry.get(key)]
            if missing:
                raise ProfileError(f"source 필수 항목 누락 {missing} ({path})")
            if entry["id"] in self._by_id:
                raise ProfileError(f"source id 중복: {entry['id']} ({path})")
            self._by_id[str(entry["id"])] = entry
        self._data = data
        self.path = path

    # --- 정체성 ---
    @property
    def univ_id(self) -> str:
        return str(self._data["univ_id"])

    @property
    def univ_name(self) -> str:
        return str(self._data["univ_name"])

    @property
    def name_prefixes(self) -> tuple[str, ...]:
        """본문이 규정명을 줄여 쓸 때 떼는 접두 ('전남대학교 학칙' → '학칙')."""
        return tuple(str(value) for value in self._data["name_prefixes"])

    @property
    def display_name(self) -> str:
        return str(self._data["service"]["display_name"])

    @property
    def prompt_identity(self) -> str:
        """답변 엔진 LLM 프롬프트의 첫 문장. 근거 밖 서술을 막는 역할이 붙어 있다."""
        return str(self._data["service"]["prompt_identity"])

    @property
    def portal_tagline(self) -> str:
        return str(self._data["service"]["portal_tagline"])

    # --- 출처 ---
    def source(self, source_id: str) -> dict:
        """소스 하나를 id로 꺼낸다. 없으면 예외 — 조용히 None을 흘리지 않는다."""
        try:
            return self._by_id[source_id]
        except KeyError:
            raise ProfileError(
                f"source id '{source_id}' 없음. 있는 id: {sorted(self._by_id)} ({self.path})"
            ) from None

    @property
    def sources(self) -> tuple[dict, ...]:
        return tuple(self._data["sources"])

    def sources_by_authority(self, authority: str) -> tuple[dict, ...]:
        """`canonical` 소스만, 또는 `mirror` 소스만 고른다.

        mirror는 단독으로 현행성을 주장하지 못한다 — 같은 규정이 canonical에도
        있으면 canonical의 날짜·상태가 이긴다. (경북대 학칙공포가 자체 폐지본
        31건을 '현행'으로 게시 중인 실측에서 나온 구분.)
        """
        return tuple(s for s in self._data["sources"]
                     if str(s.get("authority", CANONICAL)) == authority)

    @property
    def guideline_host(self) -> str:
        return str(self.source("guideline")["host"])

    @property
    def regulation_host(self) -> str:
        return str(self.source("regulation")["host"])

    def key_param_for_host(self, hostname: str) -> str | None:
        """URL 질의에서 source_key와 대조할 파라미터 이름.

        학칙공포(law.go.kr)는 `schlPubRulSeq`, 대학 게시판은 `key`처럼 서로 다르다.
        호스트가 허용 목록에 없으면 None — 호출부가 검증 실패로 처리한다.
        """
        hostname = (hostname or "").lower().rstrip(".")
        for entry in self._data["sources"]:
            host = str(entry["host"]).lower()
            if hostname == host or hostname.endswith("." + host):
                return str(entry["key_param"])
        return None

    # --- 계층 판정 ---
    @property
    def tier_min_length(self) -> int:
        """규정 계층으로 보는 source_key 최소 길이 (학칙공포 13자리 vs 게시판 4자리)."""
        return int(self._data["tier_rule"]["min_length"])


def profile_path(profile_id: str) -> Path:
    return PROFILE_DIR / f"{profile_id}.json"


def load_profile(profile_id: str) -> Profile:
    path = profile_path(profile_id)
    if not path.exists():
        raise ProfileError(f"프로필을 찾을 수 없습니다: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"프로필을 읽지 못했습니다: {path} — {exc}") from exc
    return Profile(data, path)


_ACTIVE: Profile | None = None


def active_profile() -> Profile:
    """현재 서비스의 프로필. 프로세스당 1회만 읽는다 (조문 루프에서 호출된다)."""
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = load_profile(os.environ.get("RULE_COMPASS_PROFILE", DEFAULT_PROFILE_ID))
    return _ACTIVE


def reset_active_profile() -> None:
    """테스트에서 다른 프로필로 갈아끼울 때만 쓴다."""
    global _ACTIVE
    _ACTIVE = None
