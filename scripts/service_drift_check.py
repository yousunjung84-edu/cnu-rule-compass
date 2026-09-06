#!/usr/bin/env python3
"""ops/protected_services.json 선언과 실제 Cloud Run 설정을 대조한다.

설계 원칙 — 조용한 실패 금지:
  조회가 실패하거나 서비스가 0개면 "정상"을 출력하지 않고 exit 2 로 끝낸다.
  (2026-09-02: 감사기 내부가 죽고도 "누수 0건"을 출력한 사고가 있었다.)

종료코드: 0 = 드리프트 없음 / 1 = 드리프트 있음 / 2 = 검사 자체 실패
"""
import json
import subprocess
import sys
from pathlib import Path

GCLOUD = "/opt/homebrew/bin/gcloud"
DECL = Path(__file__).resolve().parent.parent / "ops" / "protected_services.json"
CLEAN_TOKEN = "드리프트 0건"


def run(args):
    r = subprocess.run([GCLOUD, *args], capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"gcloud 실패: {' '.join(args)}\n{r.stderr.strip()[:300]}")
    return r.stdout


def actual_config(project, region, svc):
    raw = run(["run", "services", "describe", svc,
               "--project", project, "--region", region, "--format", "json"])
    d = json.loads(raw)
    tpl = d["spec"]["template"]
    ann = tpl["metadata"].get("annotations", {})
    lim = tpl["spec"]["containers"][0].get("resources", {}).get("limits", {})
    tags = [t["tag"] for t in d.get("status", {}).get("traffic", []) if t.get("tag")]
    # 값이 아니라 **소비 코드가 보는 존재**를 따진다(2026-09-07 두 리뷰 반영):
    # - 공백뿐인 값은 없는 것이다. 코어는 .strip() 뒤 폴백하고 ALLOWED_HOSTS는
    #   콤마 분리 뒤 빈 배열이 된다 — "   " 가 통과하면 검사가 거짓 안심을 준다.
    # - valueFrom(Secret Manager·ConfigMap 참조)은 값을 여기서 못 보지만 존재한다.
    env = {}
    for e in tpl["spec"]["containers"][0].get("env", []):
        if "valueFrom" in e:
            env[e["name"]] = "<valueFrom>"
        else:
            env[e["name"]] = str(e.get("value", "")).strip()
    return {
        "min_instances": ann.get("autoscaling.knative.dev/minScale", "0"),
        "cpu": lim.get("cpu", ""),
        "memory": lim.get("memory", ""),
        "tags": tags,
        "env": env,
    }


def main():
    if not DECL.exists():
        print(f"[실패] 선언 파일 없음: {DECL}", file=sys.stderr)
        return 2
    decl = json.loads(DECL.read_text(encoding="utf-8"))
    project, region = decl["project"], decl["region"]

    names = [n for n in run(["run", "services", "list", "--project", project,
                             "--region", region, "--format", "value(metadata.name)"]).split()]
    if not names:
        print("[실패] 서비스 목록이 비어 있다 — 조회 실패로 간주한다", file=sys.stderr)
        return 2

    print(f"=== 서비스 설정 드리프트 검사 ({project} / {region}, {len(names)}개) ===")
    drift = []
    for svc in sorted(names):
        spec = decl["services"].get(svc)
        tier = spec["tier"] if spec else decl["default_tier"]
        expect = spec["expect"] if spec else decl["experimental_expect"]
        act = actual_config(project, region, svc)

        for key in ("min_instances", "cpu", "memory"):
            want = expect.get(key)
            if want is None:
                continue  # 실험 등급은 cpu/memory 미고정
            if act[key] != want:
                drift.append(f"  ⚠️ {svc} [{tier}] {key}: 선언={want} / 실제={act[key]}")

        # env_required — **값이 아니라 존재**를 본다 (2026-09-07, 코어 전환 P04).
        # 왜 생겼나: adapter 전환 뒤 서비스 버전은 하드코딩이 아니라 환경변수로
        # 온다. 검증 배포에서 이 변수를 빠뜨렸더니 /health가 코어 엔진 버전
        # 0.6.1을 말했다 — 커넥터를 등록한 담당자들이 보는 번호의 계보가 끊긴다.
        # 빌드는 통과하고 서버도 정상 기동하므로 **아무도 못 잡는 종류**다.
        for name in expect.get("env_required", []):
            if not act["env"].get(name):
                drift.append(f"  ⚠️ {svc} [{tier}] 필수 환경변수 없음: {name}"
                             f" — 이미지 기본값이 그대로 노출된다")

        if not expect.get("tags_allowed", False) and act["tags"]:
            drift.append(f"  ⚠️ {svc} [{tier}] 상주 태그 발견: {', '.join(act['tags'])}"
                         f" — 트래픽 0%면 상시 과금 누수다")

    if drift:
        print("\n".join(drift))
        print(f"\n  드리프트 {len(drift)}건 — ops/protected_services.json 과 실제가 어긋난다.")
        print("  설정을 되돌리거나, 의도한 변경이면 선언 파일을 먼저 고칠 것.")
        return 1

    print(f"  {CLEAN_TOKEN}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # 조회 실패를 정상으로 오인하지 않는다
        print(f"[실패] 검사 자체가 실패했다: {exc}", file=sys.stderr)
        sys.exit(2)
