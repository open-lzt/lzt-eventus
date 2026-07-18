# Legal & Terms of Use

<p align="right"><b>English</b> · <a href="legal.md">Русский</a></p>

pylzt and lzt-eventus are **independent, unofficial** tools. They are not affiliated with,
endorsed by, or operated by lzt.market / lolz.team.

## What this software does

- **Read-only catalog ingestion.** It pages the public lzt.market catalog through
  the official API using *your own* API token(s), diffs successive snapshots into
  domain events, and stores them in a local append-only log.
- **Analytics automation.** It derives signals (new listing, price change,
  disappearance/sold, deal detection) and lets your own subscribers consume them.

## What this software does NOT do — and must not be used for

- **No brute force.** No password guessing, credential stuffing, or any
  authentication attack against any account or endpoint.
- **No 2FA / security bypass.** No circumvention of two-factor authentication,
  captchas as an attack, or any access control.
- **No account takeover, scraping of private data, or evasion of platform limits.**
- It is **catalog read + analytics only**, plus an outbound delivery layer for
  events you are entitled to receive.

## Your responsibilities

- You are solely responsible for complying with the
  **[lzt.market / lolz.team Terms of Service](https://lzt.market)** and all
  applicable laws in your jurisdiction.
- Use only API tokens issued to you. Respect rate limits and fair-use policies.
- The rate limiter and token pool exist to keep you within fair use — do not
  reconfigure them to abuse the platform.

## No warranty

This software is provided "as is", without warranty of any kind, under the
[MIT License](../LICENSE). The authors and contributors are not liable for any
account action, ban, loss, or damages arising from its use. Using it against a
platform's ToS is at your own risk.

> If you cannot use lzt.market's API within its Terms of Service, do not use this
> software.
