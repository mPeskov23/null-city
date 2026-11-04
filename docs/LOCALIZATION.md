# Russian, English and Spanish

The language selector is available on the opening cover, menus, game, club and
policy/status pages. The choice is persisted separately from campaign saves under
`nullcity-language`. Switching saves the current campaign then reloads the UI;
accounts, mission IDs, command syntax, IPs, quiz option indices and server scoring
remain independent of the display language.

`locales/source.json` contains source strings grouped by feature. Each corresponding
`.txt` file has one `English|Spanish` row per source item; literal `\n` represents a
newline. `extra.json` holds supplementary labels. Run `npm run i18n:build` after
editing. The compiler rejects missing/empty rows and derives rich-text fragments
and uppercase labels. The generated browser catalog has no external requests.

`src/i18n.js` localizes presentation text, accessible labels and placeholders using
exact catalog entries and longest matching fragments for dynamic sentences. A
MutationObserver handles existing DOM renderers; it ignores scripts, style,
textarea values, user-authored content and terminal command echoes. Canvas map
labels and exported report prose use explicit translation calls. Notes and commands
are preserved. The opening animation starts from a complete localized sentence,
rather than trying to translate partial typewriter text.

When adding text, register it in the catalog and provide both translations.
Mark user content `translate="no"`. Do not use translated text as a state identifier
or for control flow. Story portrait selection uses the canonical title stored in a
data attribute. Model tests exercise gameplay with unchanged canonical data;
localization tests check every registered source, command/IP preservation and
composed counters/dialogue fragments. Manual browser checks remain necessary for
layout, idiomatic wording and newly authored text not yet registered.
