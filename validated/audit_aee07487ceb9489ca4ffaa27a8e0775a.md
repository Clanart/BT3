### Title
Polynomial-time regex backtracking in `GPBUtil::camel2underscore` during JSON `FieldMask` parsing - (File: `php/src/Google/Protobuf/Internal/GPBUtil.php`)

### Summary
The upstream report concerns `hosted-git-info`'s `fromUrl` regex (`shortcutMatch`), where an attacker-controlled URL string is matched against a regex containing overlapping/ambiguous quantifiers, producing polynomial-time backtracking (ReDoS) reachable through a public parsing entry point with no length/complexity limit on the input before the regex runs.

The closest actual Protobuf analog is in the PHP runtime's well-known-type JSON handling: `GPBUtil::camel2underscore()` [1](#0-0)  is invoked from `GPBUtil::parseFieldMask()` [2](#0-1)  to convert each attacker-supplied `FieldMask.paths` path segment (a JSON string field on the well-known `google.protobuf.FieldMask` type) from camelCase to snake_case using `preg_match_all` with the pattern `!([A-Z][A-Z0-9]*(?=$|[A-Z][a-z0-9])|[A-Za-z][a-z0-9]+)!`.

### Finding Description
`hosted-git-info`'s failed invariant was: a regex applied to fully attacker-controlled, unbounded-length input had no complexity/length guard, and its alternation/quantifier structure allowed super-linear backtracking on crafted strings, so ordinary usage of the exposed `fromUrl` API could hang the process.

The Protobuf analog transfers the same invariant failure to `camel2underscore`: this function is reached whenever a client submits ProtoJSON containing a `FieldMask`-typed field (a supported public parsing surface, e.g., `google.protobuf.FieldMask` used directly or nested in any message via JSON parsing) with a `paths` string that is split on `,` and `.` and then regex-processed per segment with no length cap, character-class restriction, or timeout before the regex executes [2](#0-1) . The alternation `[A-Z][A-Z0-9]*(?=$|[A-Z][a-z0-9])` greedily consumes runs of uppercase/digit characters and then must backtrack character-by-character to satisfy the lookahead when the input doesn't match the expected camelCase transition, and `preg_match_all` retries the whole alternation at every start offset in the segment. For crafted segments (e.g., long runs of uppercase letters that never satisfy the lookahead, followed by a mismatching tail), this produces at least quadratic-time behavior in segment length, mirroring the "polynomial worst-case time complexity" characterization of CVE-2021-23362.

### Impact Explanation
An attacker who can submit ProtoJSON to any PHP service that parses a message containing a `FieldMask` field can supply a single, moderately-sized `paths` string segment engineered to maximize backtracking, causing CPU-bound stalls in the parsing thread. This is a targeted, low-payload availability degradation of the request-handling worker, consistent with the CWE-400 classification of the source report, rather than memory exhaustion or huge-input flooding (which are explicitly excluded from scope).

### Likelihood Explanation
Likelihood is moderate: `FieldMask` is a commonly used well-known type in JSON APIs (e.g., partial update masks), and the vulnerable code path is reached through the standard, documented JSON parsing entry point with no schema, credential, or privilege prerequisites — an ordinary client request is sufficient. However, I was not able to fully verify from the indexed content how large a crafted `paths` string needs to be to produce a measurable stall, nor confirm the exact asymptotic behavior (quadratic vs. worse) without running a local benchmark, since dynamic execution isn't available in this environment.

### Recommendation
- Impose an explicit maximum length on each `FieldMask` path segment before it reaches `camel2underscore`, mirroring the bounded-length checks already present elsewhere in Protobuf's untrusted-input handling (e.g., recursion-limit guidance in `text_format.py`/`text_format.cc`).
- Replace the backtracking-prone alternation in `camel2underscore` with a linear-time hand-written scanner (consistent with the project's own rationale for avoiding regexes in performance/security-sensitive parsing, as documented in `src/google/protobuf/io/tokenizer.cc` [3](#0-2) ), or rewrite the pattern to remove the ambiguous lookahead-driven backtracking.
- Add a regression test with a crafted long uppercase-run `paths` segment and assert bounded parse time.

### Proof of Concept
Conceptual (not executed in this environment): construct a ProtoJSON payload for any message containing a `google.protobuf.FieldMask` field whose `paths` value is a single comma-free segment consisting of a long run of uppercase letters not forming a valid camelCase boundary, e.g. a segment of the form `"A"*N` repeated with an occasional lowercase letter placed to maximize failed lookahead attempts, then call PHP `json_format`'s `Parse()` (which internally calls `GPBUtil::parseFieldMask` → `camel2underscore`) [4](#0-3) , and measure wall-clock time growth as `N` increases. I could not execute this PoC or confirm exact timing curves given the current read-only, non-executing environment — this should be validated with an actual PHP runtime benchmark before treating the severity as confirmed.

### Citations

**File:** php/src/Google/Protobuf/Internal/GPBUtil.php (L19-29)
```php
function camel2underscore($input) {
    preg_match_all(
        '!([A-Z][A-Z0-9]*(?=$|[A-Z][a-z0-9])|[A-Za-z][a-z0-9]+)!',
        $input,
        $matches);
    $ret = $matches[0];
    foreach ($ret as &$match) {
        $match = $match == strtoupper($match) ? strtolower($match) : lcfirst($match);
    }
    return implode('_', $ret);
}
```

**File:** php/src/Google/Protobuf/Internal/GPBUtil.php (L445-591)
```php
    public static function parseTimestamp($timestamp)
    {
        // prevent parsing timestamps containing with the non-existent year "0000"
        // DateTime::createFromFormat parses without failing but as a nonsensical date
        if (substr($timestamp, 0, 4) === "0000") {
            throw new \Exception("Year cannot be zero.");
        }
        // prevent parsing timestamps ending with a lowercase z
        if (substr($timestamp, -1, 1) === "z") {
            throw new \Exception("Timezone cannot be a lowercase z.");
        }

        $nanoseconds = 0;
        $periodIndex = strpos($timestamp, ".");
        if ($periodIndex !== false) {
            $nanosecondsLength = 0;
            // find the next non-numeric character in the timestamp to calculate
            // the length of the nanoseconds text
            for ($i = $periodIndex + 1, $length = strlen($timestamp); $i < $length; $i++) {
                if (!is_numeric($timestamp[$i])) {
                    $nanosecondsLength = $i - ($periodIndex + 1);
                    break;
                }
            }
            if ($nanosecondsLength % 3 !== 0) {
                throw new \Exception("Nanoseconds must be disible by 3.");
            }
            if ($nanosecondsLength > 9) {
                throw new \Exception("Nanoseconds must be in the range of 0 to 999,999,999 nanoseconds.");
            }
            if ($nanosecondsLength > 0) {
                $nanoseconds = substr($timestamp, $periodIndex + 1, $nanosecondsLength);
                $nanoseconds = intval($nanoseconds);

                if ($nanosecondsLength < 9) {
                    $nanoseconds = $nanoseconds * pow(10, 9 - $nanosecondsLength);
                }

                // remove the nanoseconds and preceding period from the timestamp
                $date = substr($timestamp, 0, $periodIndex);
                $timezone = substr($timestamp, $periodIndex + $nanosecondsLength + 1);
                $timestamp = $date.$timezone;
            }
        }

        $date = \DateTime::createFromFormat(\DateTime::RFC3339, $timestamp, new \DateTimeZone("UTC"));
        if ($date === false) {
            throw new \Exception("Invalid RFC 3339 timestamp.");
        }

        $value = new \Google\Protobuf\Timestamp();
        $seconds = $date->format("U");
        $value->setSeconds($seconds);
        $value->setNanos($nanoseconds);
        return $value;
    }

    public static function formatTimestamp($value)
    {
        if (bccomp($value->getSeconds(), "253402300800") != -1) {
          throw new GPBDecodeException("Duration number too large.");
        }
        if (bccomp($value->getSeconds(), "-62135596801") != 1) {
          throw new GPBDecodeException("Duration number too small.");
        }
        $nanoseconds = static::getNanosecondsForTimestamp($value->getNanos());
        if (!empty($nanoseconds)) {
            $nanoseconds = ".".$nanoseconds;
        }
        $date = new \DateTime('@'.$value->getSeconds(), new \DateTimeZone("UTC"));
        return $date->format("Y-m-d\TH:i:s".$nanoseconds."\Z");
    }

    public static function parseDuration($value)
    {
        if (strlen($value) < 2 || substr($value, -1) !== "s") {
          throw new GPBDecodeException("Missing s after duration string");
        }
        $number = substr($value, 0, -1);
        if (bccomp($number, "315576000001") != -1) {
          throw new GPBDecodeException("Duration number too large.");
        }
        if (bccomp($number, "-315576000001") != 1) {
          throw new GPBDecodeException("Duration number too small.");
        }
        $pos = strrpos($number, ".");
        if ($pos !== false) {
            $seconds = substr($number, 0, $pos);
            if (bccomp($seconds, 0) < 0) {
                $nanos = bcmul("0" . substr($number, $pos), -1000000000);
            } else {
                $nanos = bcmul("0" . substr($number, $pos), 1000000000);
            }
        } else {
            $seconds = $number;
            $nanos = 0;
        }
        $duration = new Duration();
        $duration->setSeconds($seconds);
        $duration->setNanos($nanos);
        return $duration;
    }

    public static function formatDuration($value)
    {
        if (bccomp($value->getSeconds(), '315576000001') != -1) {
            throw new GPBDecodeException('Duration number too large.');
        }
        if (bccomp($value->getSeconds(), '-315576000001') != 1) {
            throw new GPBDecodeException('Duration number too small.');
        }

        $nanos = $value->getNanos();
        if ($nanos === 0) {
            return (string) $value->getSeconds();
        }

        if ($nanos % 1000000 === 0) {
            $digits = 3;
        } elseif ($nanos % 1000 === 0) {
            $digits = 6;
        } else {
            $digits = 9;
        }

        $nanos = bcdiv($nanos, '1000000000', $digits);
        return bcadd($value->getSeconds(), $nanos, $digits);
    }

    public static function parseFieldMask($paths_string)
    {
        $field_mask = new FieldMask();
        if (strlen($paths_string) === 0) {
            return $field_mask;
        }
        $path_strings = explode(",", $paths_string);
        $paths = $field_mask->getPaths();
        foreach($path_strings as &$path_string) {
            $field_strings = explode(".", $path_string);
            foreach($field_strings as &$field_string) {
                $field_string = camel2underscore($field_string);
            }
            $path_string = implode(".", $field_strings);
            $paths[] = $path_string;
        }
        return $field_mask;
    }
```

**File:** src/google/protobuf/io/tokenizer.cc (L24-55)
```text
//
// The next approach that any good software engineer should look at is to
// use regular expressions.  And, indeed, I did.  I have code which
// implements this same class using regular expressions.  It's about 200
// lines shorter.  However:
// - Rather than error messages telling you "This string has an invalid
//   escape sequence at line 5, column 45", you get error messages like
//   "Parse error on line 5".  Giving more precise errors requires adding
//   a lot of code that ends up basically as complex as the hand-coded
//   version anyway.
// - The regular expression to match a string literal looks like this:
//     kString  = new RE("(\"([^\"\\\\]|"              // non-escaped
//                       "\\\\[abfnrtv?\"'\\\\0-7]|"   // normal escape
//                       "\\\\x[0-9a-fA-F])*\"|"       // hex escape
//                       "\'([^\'\\\\]|"        // Also support single-quotes.
//                       "\\\\[abfnrtv?\"'\\\\0-7]|"
//                       "\\\\x[0-9a-fA-F])*\')");
//   Verifying the correctness of this line noise is actually harder than
//   verifying the correctness of ConsumeString(), defined below.  I'm not
//   even confident that the above is correct, after staring at it for some
//   time.
// - PCRE is fast, but there's still more overhead involved than the code
//   below.
// - Sadly, regular expressions are not part of the C standard library, so
//   using them would require depending on some other library.  For the
//   open source release, this could be really annoying.  Nobody likes
//   downloading one piece of software just to find that they need to
//   download something else to make it work, and in all likelihood
//   people downloading Protocol Buffers will already be doing so just
//   to make something else work.  We could include a copy of PCRE with
//   our code, but that obligates us to keep it up-to-date and just seems
//   like a big waste just to save 200 lines of code.
```
