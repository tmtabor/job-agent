# Security Policy

## Reporting a vulnerability

Please report security issues privately through GitHub's
[**Report a vulnerability**](https://github.com/tmtabor/job-agent/security/advisories/new)
button (repository **Security** tab → **Advisories**). Do not open a public issue for
security problems.

You should get an acknowledgement within a few days. If a fix is warranted, it will be
developed under a private advisory and disclosed once a patch is available.

## Scope

In scope: the pipeline and agent code in this repository — profile loading and validation,
the source clients (Greenhouse/Lever/Ashby/Adzuna/JobSpy/Tavily), the two LLM agents, the
SQLite state layer, and the Postmark email delivery path.

Out of scope: vulnerabilities in the third-party services this project calls (the model
provider, Adzuna, Tavily, Postmark, Logfire, the ATS APIs) or in their SDKs — report those to
the respective vendors. Misconfiguration of your own deployment (leaked secrets, a
`PROFILE_YAML_B64` secret committed by mistake) is also out of scope.

## Supported versions

Only the latest `main` receives fixes.
