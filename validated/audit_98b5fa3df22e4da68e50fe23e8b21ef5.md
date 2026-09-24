## Analysis Summary

The Spring `Pattern.compile` report describes classic **ReDoS (CWE-1333)**: a regex with ambiguous/nested repetition groups causes catastrophic backtracking on adversarial input, consuming CPU. The invariant that fails is "matching cost must stay near-linear in input length"; the attacker-controlled value is the string fed to the regex engine; the missing check is any bound on backtracking/complexity before matching untrusted data.

Searching the repo for regex usage on the public parsing paths (C# `JsonParser`, Python `text_format`/`json_format`, C++ `json/internal/parser.cc`, upb `jsondec_timestamp`) shows that **all binary/ProtoJSON well-known-type parsers (Timestamp/Duration) use hand-written, bounded-length character scanning, not regex** — e.g. C++'s `ParseTimestamp`/`ParseDuration` in [1](#0-0)  and upb's `jsondec_timestamp` in [2](#0-1) . The C# `JsonParser`'s `TimestampRegex`/`DurationRegex` [3](#0-2)  use only fixed-width bounded quantifiers (`{1,9}`, `{1,12}`) with no overlapping/nested repetition — these cannot exhibit catastrophic backtracking.

The one place with a genuinely regex-driven, ambiguous nested-quantifier pattern parsing **untrusted, attacker-controlled text** is Python's `text_format.Tokenizer._TOKEN`, specifically the quoted-string sub-pattern: [4](#0-3) 

```python
_TOKEN = re.compile(
    '|'.join(
        [
            r'[a-zA-Z_][0-9a-zA-Z_+-]*',
            r'([0-9+-]|(\.[0-9]))[0-9a-zA-Z_.+-]*',
        ]
        + [
            # Avoid backtracking! https://stackoverflow.com/a/844267
            r'{qt}[^{qt}\n\\]*((\\.)+[^{qt}\n\\]*)*({qt}|\\?$)'.format(
                qt=mark
            )
            for mark in _QUOTES
        ]
    )
)
```

The comment cites the well-known technique for writing a *non-backtracking* quoted-string regex, whose safe canonical form is `"[^"\\]*(?:\\.[^"\\]*)*"` (single `\\.` per escape, non-capturing, no repetition wrapped around the escape unit itself). The code here instead wraps the escape unit in `(\\.)+` inside the outer `(...)*`, i.e. `((\\.)+[^"\n\\]*)*`. This reintroduces the classic **nested-quantifier ambiguity** ((a+)+-style): a run of `\` characters can be partitioned into escape pairs in exponentially many ways across the outer/inner repetition, and when the string is *not* well-formed (no terminating quote, and the line doesn't end in a bare `\` matching `\\?$`), the engine must exhaust all partitions before failing — classic catastrophic-backtracking blowup.

### Title
Catastrophic-backtracking regex in Python TextFormat tokenizer's quoted-string pattern - (File: python/google/protobuf/text_format.py)

### Summary
`Tokenizer._TOKEN` in the pure-Python `text_format` module builds its quoted-string alternative as `{qt}[^{qt}\n\\]*((\\.)+[^{qt}\n\\]*)*({qt}|\\?$)`. The nested repetition `((\\.)+...)*` is ambiguous for runs of backslashes, so a crafted, unterminated quoted string dominated by backslash characters can force exponential-time backtracking in Python's `re` engine when `google.protobuf.text_format.Parse`/`Merge` is called on attacker-controlled text, analogous to CVE-2009-1190's regex-complexity DoS.

### Finding Description
`text_format.Parse`/`Merge` is a supported public parsing API that tokenizes arbitrary input text via `Tokenizer`, whose `_TOKEN` regex includes, per quote mark, `qt + "[^" + qt + "\n\\]*" + "((\\.)+[^" + qt + "\n\\]*)*" + "(" + qt + "|\\?$)"` [5](#0-4) . The safe idiom referenced by the inline comment ("Avoid backtracking! https://stackoverflow.com/a/844267") is `[^"\\]*(?:\\.[^"\\]*)*"`, which is unambiguous because each escape unit `\\.` is matched exactly once per outer iteration. Here the escape unit itself is wrapped in `+` (`(\\.)+`) *inside* the outer `*`, so a sequence of `2k` backslash characters can be decomposed by the regex engine into many equivalent ways of splitting work between the inner `+` and outer `*` groups. Since regex backtracking explores all these decompositions when the tail alternative `({qt}|\\?$)` fails to match (e.g., the value never closes with a quote and doesn't end in a lone `\`), match time can grow exponentially with the number of backslashes.

### Impact Explanation
An ordinary client submitting bounded TextFormat input to `text_format.Parse`/`Merge` (e.g., a string field value crafted with a long run of backslashes and no closing quote) can drive the tokenizer into worst-case exponential regex matching, causing CPU-bound denial of service in the parsing process — the same class of impact as CVE-2009-1190 (CPU consumption via crafted string against Pattern.compile-based parsing).

### Likelihood Explanation
`text_format.Parse` is a documented, commonly used public API for accepting human/attacker-supplied protobuf text (e.g., debug endpoints, config loaders, RPC reflection tools). No length or content restriction on quoted-string escapes prevents the attacker from choosing an adversarial backslash run, and the vulnerable alternative is always attempted whenever a token begins with a quote character.

### Recommendation
Replace `((\\.)+[^{qt}\n\\]*)*` with the canonical non-ambiguous form `(?:\\.[^{qt}\n\\]*)*` (single-escape unit per outer iteration, non-capturing) to restore true linear-time matching as originally intended by the cited technique. Additionally, consider bounding maximum token length before invoking the regex tokenizer on untrusted TextFormat input.

### Proof of Concept
Conceptual reproduction (not executed): construct a TextFormat field value beginning with an unterminated quoted string composed of a large even count of backslash characters, e.g. a text-proto payload where a string field starts with `"` followed by `N` backslashes (N in the low hundreds) and no terminating quote character, fed to `text_format.Parse(payload, SomeMessage())`. Because the constructed value never satisfies `({qt}|\\?$)`, `re` must backtrack across the ambiguous `((\\.)+[^"\n\\]*)*` decompositions before failing, with matching time increasing rapidly (consistent with textbook exponential-backtracking regex patterns) as N grows — this should be verified empirically with `timeit` against increasing N to confirm super-linear growth before treating it as confirmed exploitable. [6](#0-5)

### Citations

**File:** src/google/protobuf/json/internal/parser.cc (L832-935)
```text
absl::Status ParseTimestamp(JsonLexer& lex, const Desc<Traits>& desc,
                            Msg<Traits>& msg) {
  if (lex.Peek(JsonLexer::kNull)) {
    return lex.Expect("null");
  }

  absl::StatusOr<LocationWith<MaybeOwnedString>> str = lex.ParseUtf8();
  RETURN_IF_ERROR(str.status());

  absl::string_view data = str->value.AsView();
  if (data.size() < 20) {
    return str->loc.Invalid("timestamp string too short");
  }

  int64_t secs;
  {
    /* 1972-01-01T01:00:00 */
    auto year = TakeTimeDigitsWithSuffixAndAdvance(data, 4, "-");
    if (!year.has_value() || *year == 0) {
      return str->loc.Invalid("bad year in timestamp");
    }
    auto mon = TakeTimeDigitsWithSuffixAndAdvance(data, 2, "-");
    if (!mon.has_value() || *mon == 0) {
      return str->loc.Invalid("bad month in timestamp");
    }
    auto day = TakeTimeDigitsWithSuffixAndAdvance(data, 2, "T");
    if (!day.has_value() || *day == 0) {
      return str->loc.Invalid("bad day in timestamp");
    }
    auto hour = TakeTimeDigitsWithSuffixAndAdvance(data, 2, ":");
    if (!hour.has_value()) {
      return str->loc.Invalid("bad hours in timestamp");
    }
    auto min = TakeTimeDigitsWithSuffixAndAdvance(data, 2, ":");
    if (!min.has_value()) {
      return str->loc.Invalid("bad minutes in timestamp");
    }
    auto sec = TakeTimeDigitsWithSuffixAndAdvance(data, 2, "");
    if (!sec.has_value()) {
      return str->loc.Invalid("bad seconds in timestamp");
    }

    uint32_t m_adj = *mon - 3;  // March-based month.
    uint32_t carry = m_adj > *mon ? 1 : 0;

    uint32_t year_base = 4800;  // Before min year, multiple of 400.
    uint32_t y_adj = *year + year_base - carry;

    uint32_t month_days = ((m_adj + carry * 12) * 62719 + 769) / 2048;
    uint32_t leap_days = y_adj / 4 - y_adj / 100 + y_adj / 400;
    int32_t epoch_days =
        y_adj * 365 + leap_days + month_days + (*day - 1) - 2472632;

    secs = int64_t{epoch_days} * 86400 + *hour * 3600 + *min * 60 + *sec;
  }

  auto nanos = TakeNanosAndAdvance(data);
  if (!nanos.has_value()) {
    return str->loc.Invalid("timestamp had bad nanoseconds");
  }

  if (data.empty()) {
    return str->loc.Invalid("timestamp missing timezone offset");
  }

  {
    // [+-]hh:mm or Z
    bool neg = false;
    switch (data[0]) {
      case '-':
        neg = true;
        ABSL_FALLTHROUGH_INTENDED;
      case '+': {
        if (data.size() != 6) {
          return str->loc.Invalid("timestamp offset of wrong size.");
        }

        data = data.substr(1);
        auto hour = TakeTimeDigitsWithSuffixAndAdvance(data, 2, ":");
        auto mins = TakeTimeDigitsWithSuffixAndAdvance(data, 2, "");
        if (!hour.has_value() || !mins.has_value()) {
          return str->loc.Invalid("timestamp offset has bad hours and minutes");
        }

        int64_t offset = (*hour * 60 + *mins) * 60;
        secs += (neg ? offset : -offset);
        break;
      }
      // Lowercase z is not accepted, per the spec.
      case 'Z':
        if (data.size() == 1) {
          break;
        }
        ABSL_FALLTHROUGH_INTENDED;
      default:
        return str->loc.Invalid("bad timezone offset");
    }
  }

  Traits::SetInt64(Traits::MustHaveField(desc, 1), msg, secs);
  Traits::SetInt32(Traits::MustHaveField(desc, 2), msg, *nanos);

  return absl::OkStatus();
}
```

**File:** php/ext/google/protobuf/php-upb.c (L6059-6125)
```c
static void jsondec_timestamp(jsondec* d, upb_Message* msg,
                              const upb_MessageDef* m) {
  UPB_ASSERT(!upb_Message_IsFrozen(msg));
  upb_MessageValue seconds;
  upb_MessageValue nanos;
  upb_StringView str = jsondec_string(d);
  const char* ptr = str.data;
  const char* end = ptr + str.size;

  if (str.size < 20) goto malformed;

  {
    /* 1972-01-01T01:00:00 */
    int year = jsondec_tsdigits(d, &ptr, 4, "-");
    int mon = jsondec_tsdigits(d, &ptr, 2, "-");
    int day = jsondec_tsdigits(d, &ptr, 2, "T");
    int hour = jsondec_tsdigits(d, &ptr, 2, ":");
    int min = jsondec_tsdigits(d, &ptr, 2, ":");
    int sec = jsondec_tsdigits(d, &ptr, 2, NULL);

    seconds.int64_val = jsondec_unixtime(year, mon, day, hour, min, sec);
  }

  nanos.int32_val = jsondec_nanos(d, &ptr, end);

  {
    /* [+-]08:00 or Z */
    int ofs_hour = 0;
    int ofs_min = 0;
    bool neg = false;

    if (ptr == end) goto malformed;

    switch (*ptr++) {
      case '-':
        neg = true;
        /* fallthrough */
      case '+':
        if ((end - ptr) != 5) goto malformed;
        ofs_hour = jsondec_tsdigits(d, &ptr, 2, ":");
        ofs_min = jsondec_tsdigits(d, &ptr, 2, NULL);
        ofs_min = ((ofs_hour * 60) + ofs_min) * 60;
        seconds.int64_val += (neg ? ofs_min : -ofs_min);
        break;
      case 'Z':
        if (ptr != end) goto malformed;
        break;
      default:
        goto malformed;
    }
  }

  if (seconds.int64_val < -62135596800) {
    jsondec_err(d, "Timestamp out of range");
  }

  jsondec_checkoom(
      d, upb_Message_SetFieldByDef(msg, upb_MessageDef_FindFieldByNumber(m, 1),
                                   seconds, d->arena));
  jsondec_checkoom(
      d, upb_Message_SetFieldByDef(msg, upb_MessageDef_FindFieldByNumber(m, 2),
                                   nanos, d->arena));
  return;

malformed:
  jsondec_err(d, "Malformed timestamp");
}
```

**File:** csharp/src/Google.Protobuf/JsonParser.cs (L42-43)
```csharp
        private static readonly Regex TimestampRegex = new Regex(@"^(?<datetime>[0-9]{4}-[01][0-9]-[0-3][0-9]T[012][0-9]:[0-5][0-9]:[0-5][0-9])(?<subseconds>\.[0-9]{1,9})?(?<offset>(Z|[+-][0-1][0-9]:[0-5][0-9]))$", FrameworkPortability.CompiledRegexWhereAvailable);
        private static readonly Regex DurationRegex = new Regex(@"^(?<sign>-)?(?<int>[0-9]{1,12})(?<subseconds>\.[0-9]{1,9})?s$", FrameworkPortability.CompiledRegexWhereAvailable);
```

**File:** python/google/protobuf/text_format.py (L1473-1490)
```python
  _WHITESPACE = re.compile(r'\s+')
  _COMMENT = re.compile(r'(\s*#.*$)', re.MULTILINE)
  _WHITESPACE_OR_COMMENT = re.compile(r'(\s|(#.*$))+', re.MULTILINE)
  _TOKEN = re.compile(
      '|'.join(
          [
              r'[a-zA-Z_][0-9a-zA-Z_+-]*',  # an identifier
              r'([0-9+-]|(\.[0-9]))[0-9a-zA-Z_.+-]*',  # a number
          ]
          + [  # quoted str for each quote mark
              # Avoid backtracking! https://stackoverflow.com/a/844267
              r'{qt}[^{qt}\n\\]*((\\.)+[^{qt}\n\\]*)*({qt}|\\?$)'.format(
                  qt=mark
              )
              for mark in _QUOTES
          ]
      )
  )
```
