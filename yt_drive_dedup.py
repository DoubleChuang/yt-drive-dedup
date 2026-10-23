#!/usr/bin/env python3
"""Find videos in Google Drive that are already uploaded to YouTube, and trash the Drive copies.

Auth: any Google OAuth "authorized user" token JSON with these scopes
        https://www.googleapis.com/auth/drive
        https://www.googleapis.com/auth/youtube.readonly
      Generate one with:  python auth.py

Usage:
  python yt_drive_dedup.py                       # report only (safe, default)
  python yt_drive_dedup.py --owned-only          # only files you own (skip shared files)
  python yt_drive_dedup.py --explain             # show best YouTube candidate for every file
  python yt_drive_dedup.py --apply               # trash STRONG matches
  python yt_drive_dedup.py --apply --ids 3,7,9   # trash specific report rows
  python yt_drive_dedup.py --apply --permanent   # permanent delete (irreversible!)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Token lookup order: --token arg > $GOOGLE_TOKEN_FILE > Hermes token > ./token.json
_HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def resolve_token_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("GOOGLE_TOKEN_FILE")
    candidates = [Path(env).expanduser() if env else None,
                  _HERMES_HOME / "google_token.json",
                  Path("token.json")]
    for cand in candidates:
        if cand and cand.exists():
            return cand
    return Path("token.json")

DRIVE_QUERY = (
    "trashed = false and ("
    "mimeType contains 'video/' or "
    "fileExtension in 'mp4,mov,avi,mkv,m4v,wmv,flv,webm,ts,mts,3gp,mpg,mpeg'"
    ")"
)
DRIVE_FIELDS = (
    "nextPageToken, files(id,name,size,mimeType,md5Checksum,modifiedTime,"
    "webViewLink,parents,ownedByMe,owners(emailAddress),"
    "capabilities(canTrash,canDelete),"
    "videoMediaMetadata(durationMillis,width,height))"
)

NOISE = re.compile(r"(?i)[\s_\-\.·・,，、()\[\]{}【】<>「」『』]|copy|副本|最終|final|輸出|output")
# "影片 copy 2" / "影片 (1)" / "影片_1" — extension must already be stripped before this runs.
COPY_SUFFIX = re.compile(
    r"(?i)(?:[\s_\-]*[\(\[]\s*(?:copy|副本|\d{1,2})\s*[\)\]]"
    r"|[\s_\-]+(?:copy|副本)(?:[\s_\-]*\d{1,2})?"
    r"|[\s_\-]+\d{1,2})\s*$"
)

VIDEO_EXT = r"\.(mp4|mov|avi|mkv|m4v|wmv|flv|webm|ts|mts|3gp|mpg|mpeg)$"


def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def load_creds(scopes: list[str], token_path: Path | None = None) -> Credentials:
    token_path = token_path or resolve_token_path()
    if not token_path.exists():
        die(
            f"no Google token at {token_path}\n"
            "Create one first:  python auth.py    (see README.md)"
        )
    creds = Credentials.from_authorized_user_file(str(token_path))
    granted = {s.strip() for s in (creds.scopes or []) if s.strip()}
    for need in scopes:
        if granted and need not in granted:
            die(
                f"token is missing scope {need}\n"
                "Re-authorize: delete token.json and run  python auth.py  again."
            )
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            die("token invalid and cannot be refreshed — redo OAuth setup")
    return creds


def norm(text: str) -> str:
    """Aggressive name key for matching filenames against video titles."""
    if not text:
        return ""
    s = str(text)
    s = re.sub(VIDEO_EXT, "", s, flags=re.I)
    s = COPY_SUFFIX.sub("", s)
    s = NOISE.sub("", s)
    return s.lower()


def parse_iso_duration(value: str) -> int | None:
    """PT1H2M3S -> milliseconds."""
    if not value:
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", value)
    if not m:
        return None
    d, h, mi, s = m.groups()
    total = (
        int(d or 0) * 86400 + int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)
    )
    return int(total * 1000)


def fmt_dur(ms: int | None) -> str:
    if not ms:
        return "-"
    sec = int(int(ms) / 1000)
    return f"{sec // 3600:d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def fmt_size(n: int | None) -> str:
    if n is None:
        return "-"
    v = float(int(n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or unit == "TB":
            return f"{v:.1f}{unit}"
        v /= 1024
    return "-"


def list_drive(service) -> list[dict]:
    out, token = [], None
    while True:
        resp = (
            service.files()
            .list(
                q=DRIVE_QUERY,
                fields=DRIVE_FIELDS,
                pageSize=1000,
                pageToken=token,
                orderBy="name",
            )
            .execute()
        )
        out.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return out


def list_youtube(service) -> list[dict]:
    ch = service.channels().list(part="contentDetails,snippet", mine=True).execute()
    items = ch.get("items") or []
    if not items:
        die("no YouTube channel found for this Google account")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    ids, token = [], None
    while True:
        resp = (
            service.playlistItems()
            .list(part="contentDetails", playlistId=uploads, maxResults=50, pageToken=token)
            .execute()
        )
        ids.extend(i["contentDetails"]["videoId"] for i in resp.get("items", []))
        token = resp.get("nextPageToken")
        if not token:
            break

    videos = []
    for i in range(0, len(ids), 50):
        resp = (
            service.videos()
            .list(
                part="snippet,contentDetails,fileDetails,status",
                id=",".join(ids[i : i + 50]),
            )
            .execute()
        )
        for v in resp.get("items", []):
            fd = v.get("fileDetails") or {}
            videos.append(
                {
                    "id": v["id"],
                    "title": v["snippet"]["title"],
                    "duration_ms": parse_iso_duration(v["contentDetails"].get("duration", "")),
                    "file_name": fd.get("fileName"),
                    "file_size": int(fd["fileSize"]) if fd.get("fileSize") else None,
                    "uploaded": v["snippet"].get("publishedAt"),
                    "privacy": (v.get("status") or {}).get("privacyStatus"),
                }
            )
    return videos


def score(drive: dict, yt: dict) -> tuple[int, str, list[str]]:
    """0-100 confidence that drive file == yt video, plus a label and evidence."""
    pts, why = 0, []
    d_size = int(drive["size"]) if drive.get("size") else None
    d_dur = (drive.get("videoMediaMetadata") or {}).get("durationMillis")
    d_dur = int(d_dur) if d_dur else None

    if d_size and yt["file_size"] and d_size == yt["file_size"]:
        pts += 55
        why.append("size=")
    if yt["file_name"] and yt["file_name"] == drive["name"]:
        pts += 40
        why.append("filename=")
    elif yt["file_name"] and norm(yt["file_name"]) == norm(drive["name"]):
        pts += 30
        why.append("filename~")
    elif norm(yt["title"]) == norm(drive["name"]):
        pts += 25
        why.append("title=")
    else:
        nt, nd = norm(yt["title"]), norm(drive["name"])
        # Short junk titles like "1" / "2" / "11" are substrings of almost every
        # filename, so containment only counts for real words.
        if len(nt) >= 5 and (nt in nd or nd in nt):
            pts += 15
            why.append("title~")

    if d_dur and yt["duration_ms"]:
        delta = abs(d_dur - yt["duration_ms"])
        if delta <= 2000:
            pts += 40
            why.append("dur=")
        elif delta <= 10000:
            pts += 18
            why.append("dur~")

    pts = min(pts, 100)
    if pts >= 85:
        label = "STRONG"
    elif pts >= 55:
        label = "REVIEW"
    else:
        label = ""
    return pts, label, why


def best_candidate(drive: dict, yt_videos: list[dict]):
    """Highest scoring YouTube video for one Drive file, threshold or not."""
    best = None
    for y in yt_videos:
        pts, label, why = score(drive, y)
        if best is None or pts > best[0]:
            best = (pts, label, why, y)
    return best


def build_report(drive_files: list[dict], yt_videos: list[dict]) -> tuple[list[dict], list[dict]]:
    rows, matched_yt = [], set()
    for d in drive_files:
        best = None
        for y in yt_videos:
            pts, label, why = score(d, y)
            if label and (best is None or pts > best[1]):
                best = (y, pts, label, why)
        if best:
            y, pts, label, why = best
            matched_yt.add(y["id"])
            rows.append(
                {
                    "drive_id": d["id"],
                    "drive_name": d["name"],
                    "drive_size": int(d["size"]) if d.get("size") else None,
                    "drive_dur": int((d.get("videoMediaMetadata") or {}).get("durationMillis") or 0) or None,
                    "drive_modified": d.get("modifiedTime", "")[:10],
                    "owner": ",".join(o.get("emailAddress", "") for o in (d.get("owners") or [])),
                    "connected": "yes" if d.get("ownedByMe") else "no",
                    "yt_id": y["id"],
                    "yt_title": y["title"],
                    "yt_url": f"https://youtu.be/{y['id']}",
                    "yt_filename": y["file_name"] or "",
                    "yt_dur": y["duration_ms"],
                    "confidence": pts,
                    "label": label,
                    "evidence": ",".join(why),
                }
            )
    # One YouTube video can only be the source of one Drive file that we act on.
    # When several Drive files claim the same video, keep only the one proven by
    # an exact size or original-filename match; the rest are coincidence.
    by_yt: dict[str, list[dict]] = {}
    for r in rows:
        by_yt.setdefault(r["yt_id"], []).append(r)
    dropped: list[dict] = []
    kept: list[dict] = []
    for group in by_yt.values():
        if len(group) == 1:
            kept.extend(group)
            continue
        group.sort(key=lambda r: -r["confidence"])
        proof = [r for r in group if "size=" in r["evidence"] or "filename=" in r["evidence"]]
        if proof:
            kept.append(proof[0])
            dropped.extend(r for r in group if r is not proof[0])
        else:
            dropped.extend(group)

    rows = kept
    rows.sort(key=lambda r: (-r["confidence"], r["drive_name"].lower()))
    matched_ids = {r["drive_id"] for r in rows}
    unmatched = [d for d in drive_files if d["id"] not in matched_ids]
    for r in dropped:
        r["label"] = "AMBIG"
        r["note"] = "多個 Drive 檔案搶同一支 YouTube 影片，無 size/filename 鐵證"
    return rows, unmatched, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually trash the STRONG matches")
    ap.add_argument("--permanent", action="store_true", help="permanent delete instead of trash")
    ap.add_argument("--ids", help="comma separated report row numbers to delete (overrides auto pick)")
    ap.add_argument("--show-all", action="store_true", help="also print unmatched items")
    ap.add_argument("--owned-only", action="store_true",
                    help="只有你擁有的檔案才算（略過別人分享給你的）")
    ap.add_argument("--explain", action="store_true",
                    help="列出每支影片最接近的 YouTube 候選，含未達門檻的")
    ap.add_argument("--token", metavar="PATH", help="Google OAuth token JSON 路徑")
    ap.add_argument("--report-dir", metavar="DIR", default=".",
                    help="CSV 報表輸出目錄（預設: 目前目錄）")
    args = ap.parse_args()

    scopes = [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]
    token_path = resolve_token_path(args.token)
    print(f"token: {token_path}")
    creds = load_creds(scopes, token_path)
    REPORT_PATH = Path(args.report_dir).expanduser() / "yt_drive_dedup_report.csv"
    drive_svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    yt_svc = build("youtube", "v3", credentials=creds, cache_discovery=False)

    print("reading Drive videos ...", flush=True)
    drive_files = list_drive(drive_svc)
    print(f"  {len(drive_files)} video files in Drive")
    report_path = REPORT_PATH
    if args.owned_only:
        before = len(drive_files)
        drive_files = [d for d in drive_files if d.get("ownedByMe")]
        report_path = REPORT_PATH.with_name("yt_drive_dedup_report_owned.csv")
        print(f"  -> 只看你擁有的: {len(drive_files)} 支（略過 {before - len(drive_files)} 支別人分享的）")
    print("reading YouTube uploads ...", flush=True)
    yt_videos = list_youtube(yt_svc)
    print(f"  {len(yt_videos)} videos on the channel")

    rows, unmatched, dropped = build_report(drive_files, yt_videos)

    strong = [r for r in rows if r["label"] == "STRONG"]
    review = [r for r in rows if r["label"] == "REVIEW"]

    print()
    print(f"{'#':>3}  {'信心':<4} {'證據':<20} {'權':<2} {'Drive 檔名':<42} {'大小':>9} {'時長':>9}  YouTube")
    print("-" * 158)
    for i, r in enumerate(rows, 1):
        print(
            f"{i:>3}  {r['label']:<4} {r['evidence']:<20} {('我' if r['connected']=='yes' else '他'):<2} "
            f"{r['drive_name'][:40]:<42} "
            f"{fmt_size(r['drive_size']):>9} {fmt_dur(r['drive_dur']):>9}  {r['yt_title'][:40]}  {r['yt_url']}"
        )
    print("-" * 158)
    mine = [r for r in strong if r["connected"] == "yes"]
    others = [r for r in strong if r["connected"] != "yes"]
    total_bytes = sum(r["drive_size"] or 0 for r in mine)
    print(f"STRONG {len(strong)} 筆｜你擁有可刪 {len(mine)} 筆（{fmt_size(total_bytes)}）｜別人分享、你無法刪 {len(others)} 筆")
    print(f"REVIEW {len(review)} 筆（疑似，需你確認）")
    print(f"Drive 未配對 {len(unmatched)} 筆")
    if dropped:
        print(f"AMBIG {len(dropped)} 筆（多檔搶同一支影片，已排除不刪）")

    if args.explain:
        print("\n--- 每支影片最接近的 YouTube 候選（含未達門檻，供你判斷）---")
        for d in sorted(drive_files, key=lambda x: x["name"].lower()):
            pts, label, why, y = best_candidate(d, yt_videos)
            d_dur = (d.get("videoMediaMetadata") or {}).get("durationMillis")
            print(
                f"  [{label or '----':<6}{pts:>4}] {d['name'][:38]:<40} "
                f"{fmt_size(int(d['size']) if d.get('size') else None):>9} {fmt_dur(d_dur):>9}"
                f"  v.s. {y['title'][:34]:<36} {','.join(why)}  https://youtu.be/{y['id']}"
            )

    yt_seen = {r["yt_id"] for r in rows}
    orphan_yt = [y for y in yt_videos if y["id"] not in yt_seen]

    if args.show_all:
        LIMIT = 40
        if unmatched:
            print(f"\n--- Drive 未配對（前 {min(LIMIT, len(unmatched))} / 共 {len(unmatched)}）---")
            for d in sorted(unmatched, key=lambda x: x["name"].lower())[:LIMIT]:
                print(f"  {d['name']}  ({fmt_size(int(d['size']) if d.get('size') else None)})")
        if orphan_yt:
            print(f"\n--- 只在 YouTube、Drive 已無來源（前 {min(LIMIT, len(orphan_yt))} / 共 {len(orphan_yt)}）---")
            for y in sorted(orphan_yt, key=lambda x: x["title"].lower())[:LIMIT]:
                print(f"  {y['title']}  (https://youtu.be/{y['id']})")

    # 完整未配對 / orphan 清單另外存檔，畫面只留摘要
    side = report_path.with_name("yt_drive_dedup_unmatched.csv")
    with side.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["kind", "name", "size", "duration_ms", "url_or_id"])
        for d in unmatched:
            w.writerow(["drive_no_match", d["name"], d.get("size", ""),
                        (d.get("videoMediaMetadata") or {}).get("durationMillis", ""), d["id"]])
        for y in orphan_yt:
            w.writerow(["youtube_only", y["title"], y["file_size"] or "",
                        y["duration_ms"] or "", f"https://youtu.be/{y['id']}"])
        for r in dropped:
            w.writerow(["ambiguous", r["drive_name"], r["drive_size"] or "",
                        r["drive_dur"] or "", f"https://youtu.be/{r['yt_id']}"])

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_fields = [
        "drive_id", "drive_name", "drive_size", "drive_dur", "drive_modified",
        "owner", "connected", "yt_id", "yt_title", "yt_url", "yt_filename",
        "yt_dur", "confidence", "label", "evidence",
    ]
    with report_path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=report_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\n報表: {report_path}")

    if not args.apply:
        print("\n(報告模式，未刪除任何東西。加 --apply 才會刪 STRONG 那批)")
        return

    if args.ids:
        wanted = {int(x) for x in args.ids.replace(" ", "").split(",") if x}
        targets = [r for i, r in enumerate(rows, 1) if i in wanted]
        missing = wanted - set(range(1, len(rows) + 1))
        if missing:
            die(f"row number out of range: {sorted(missing)}")
    else:
        targets = strong

    if not targets:
        print("沒有可刪的項目。")
        return

    not_mine = [r for r in targets if r.get("connected") == "no"]
    targets = [r for r in targets if r.get("connected") != "no"]
    if not_mine:
        print(f"\n跳過 {len(not_mine)} 個別人的檔案（你沒有刪除權限，需擁有者自行處理）:")
        for r in not_mine:
            print(f"  - {r['drive_name']}  (擁有者: {r['owner'] or '未知'})")

    if not targets:
        print("沒有你有權刪除的項目。")
        return

    mode = "永久刪除" if args.permanent else "移到垃圾桶"
    print(f"\n準備{mode} {len(targets)} 個 Drive 檔案:")
    for r in targets:
        print(f"  - {r['drive_name']}  ->  {r['yt_url']}")

    ok, fail = 0, []
    for r in targets:
        try:
            if args.permanent:
                drive_svc.files().delete(fileId=r["drive_id"]).execute()
            else:
                drive_svc.files().update(fileId=r["drive_id"], body={"trashed": True}).execute()
            ok += 1
            print(f"  OK   {r['drive_name']}")
        except HttpError as e:
            fail.append((r["drive_name"], str(e)))
            print(f"  FAIL {r['drive_name']}: {e}")

    print(f"\n完成: 成功 {ok}, 失敗 {len(fail)}")
    if fail:
        for name, err in fail:
            print(f"  {name}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
