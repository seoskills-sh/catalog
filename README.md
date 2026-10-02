# seoskills.sh catalog

SEO agent skills, authored by [seoskills.sh](https://seoskills.sh), a Synup project.

Each folder here is one installable skill: a `SKILL.md` an agent follows, plus its
`scripts/` and `references/`.

## Install

Install one skill by its folder name:

```
npx skills add https://seoskills.sh --skill <skill>
```

For example:

```
npx skills add https://seoskills.sh --skill zero-click-risk-scorer
```

Keep the `https://`: the skills CLI only looks up a site's discovery index for a
full URL, and reads `seoskills.sh/<skill>` as a GitHub repository. To pick from
every skill in the index, run `npx skills add https://seoskills.sh`.

Browse the full catalog, including community skills, at https://seoskills.sh.

## License

MIT. See [LICENSE](LICENSE).
