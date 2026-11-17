# Publishing ScholarForge in plugin catalogs

The product works without any catalog: both plugins install from this repository as a marketplace (see
`plugins/README.md`). Catalog listings are an additional distribution channel.

## Anthropic plugin directory (Claude Code)

Checklist derived from the Claude Code plugin documentation and `claude plugin validate`:

- [x] `plugin.json` valid; name `scholarforge` (not reserved), version, description, author, license.
- [x] `claude plugin validate` passes for the plugin and the marketplace.
- [x] Icon: `.claude-plugin/icon.png` (square, 1024 px) — the directory reads it on first submission only.
- [ ] Listing URLs to add before submission: `documentationUrl`, `supportUrl`, `privacyPolicyUrl`,
      `termsOfServiceUrl` (https) — they require the public website, which is out of scope for now.
- [x] No top-level `bin/` directory (claude.ai and Cowork refuse plugins with one).
- [x] Sensitive options use `userConfig` with `sensitive: true` (stored in secure storage).
- [ ] Public repository hosting the marketplace (owner/repo) and a README install line:
      `/plugin install scholarforge --marketplace <owner>/<repo>`.
- Privacy statement to publish: book content stays on the user's machine; scholarly APIs receive only
  bibliographic queries; the optional OpenAI image provider receives figure prompts with the user's own
  key; the license server receives the license key, a hashed device id and the app version.

## OpenAI Codex plugin directory

- [x] `.codex-plugin/plugin.json` with `interface` (display name, descriptions, category, capabilities,
      default prompts, logo/composer icon, brand colour), `skills`, `mcpServers`.
- [x] Local marketplace `.agents/plugins/marketplace.json` with `policy` and `category`; installs with
      Codex CLI 0.146.
- [ ] `websiteURL`, `privacyPolicyURL`, `termsOfServiceURL` in `interface` before submission.
- Note: the official guide also describes a portable root `plugin.json` (`$schema`
  agent-plugins.org) with OpenAI settings under `extensions.com.openai`; the `.codex-plugin` overlay used
  here remains supported. Re-check the submission rules at submission time.

## Commercial terms in listings

ScholarForge Pro, $20/month, all features, no page or project limits; third-party API costs (e.g. OpenAI
image generation) are paid by the user directly. Follow each catalog's rules on paid plugins and external
billing; if a catalog does not allow external subscriptions, distribute through the repository
marketplace instead and do not imitate catalog support.
