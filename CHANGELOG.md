# Changelog

## v0.2.6 - 2026-10-31

- Tick roadmap step 4 (agent side done in video-production-agent #82) (#26) (2e02ee0)

## v0.2.5 - 2026-10-31

- Per-operation tool ids in provides: qc/check, qc/inspect, qc/run alias (roadmap step 2) (#25) (be038d6)

## v0.2.4 - 2026-10-30

- ci: pin third-party actions by commit SHA (#24) (26c2e82)

## v0.2.3 - 2026-10-30

- Opt-in VMAF/SSIM/PSNR reference metrics, UNKNOWN without libvmaf (roadmap step 3) (#23) (d84801b)

## v0.2.2 - 2026-10-30

- Report the pyproject.toml version everywhere instead of a stale 0.1.0 constant (#22) (4499f6d)

## v0.2.1 - 2026-10-30

- Enforce request.timeout: kill the process group, fail with TIMEOUT (#21) (dd00cf1)

## v0.2.0 - 2026-10-30

- feat: flash (BT.1702), planned-cut accuracy and text legibility checks (#20) (e72cee7)

## v0.1.4 - 2026-10-30

- Add ECOSYSTEM_ROADMAP.md: gaps, wiring and PR-sized steps from the 2026-10-30 ecosystem audit (#19) (7233538)

## v0.1.3 - 2026-10-25

- Add MIT LICENSE (adf6cfa)

## v0.1.2 - 2026-10-25

- Add the Sponsor badge and .github/FUNDING.yml (#18) (92b5a44)

## v0.1.1 - 2026-10-23

- ci(release): resolve the version with release-drafter v7 dry-run; nothing to release is a no-op (#17) (ae2c9b9)
- ci: bump GitHub Actions (checkout v7, setup-python v7, codeql v4 and others) (#16) (af1901c)
- docs(readme): fresnel-style header with a Liquid Glass app icon (#15) (56f8a94)

## v0.1.0 - 2026-09-12

- Add GitHub automation: release, PR labeling, Dependabot, CodeQL, security policy (#9) (b5097d9)
- Add duration ceiling, delivery size ceiling, color/HDR, VFR, and min-resolution checks (#8) (c6675dd)
- Phase 4: Visual Defect Evidence (luminance-range excursions) (#6) (e636c3e)
- Phase 3: Timeline Integrity (#4) (7746d7e)
- Phase 2: Cross-Artifact Validation (#3) (8d61cfb)
- Phase 1: Delivery Gate Foundation (kind=delivery_package) (#2) (fb4e60f)
- Add provides: publish Capability ids for cross-repository discovery (024591a)
- Implement qc-skill: media quality control / validation skill (11a1c2e)
- Initial commit (7597e6f)
