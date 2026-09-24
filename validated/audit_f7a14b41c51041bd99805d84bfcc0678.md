### Title
Missing upper-bound (`INT_MAX`) validation before narrowing a 64-bit varint length to a PHP `int` in `readVarintSizeAsInt` - (File: `php/src/Google/Protobuf/Internal/CodedInputStream.php`)

### Summary
The Sherlock report describes a Solidity bug where `_totalAmount` (a `uint256`) is unsafely `uint120(_totalAmount)`-cast *before* the `require(totalAmount <= type(uint120).max, ...)` check is performed — so the check is evaluated on the already-truncated value and can never fail, defeating the intended validation. The transferable invariant is: **"narrow an attacker-controlled wide value to a smaller type only after (and gated by) an explicit range check against the narrower type's bound; never check the post-truncation value."**

In the PHP implementation of protobuf, `CodedInputStream::readVarintSizeAsInt()` — the function used to read length prefixes for strings, bytes, and embedded messages — reads a full 64-bit varint and then does `(int)$var` with **no range check at all**, unlike the C++ reference implementation (`CodedInputStream::ReadVarintSizeAsIntFallback` / `ReadVarintSizeAsIntSlow`), which explicitly checks `temp > static_cast<uint64_t>(INT_MAX)` and rejects the parse (`return -1`) before ever using the value as a size.

### Finding Description
The trusted C++ reference behavior for reading a length prefix is defined in `src/google/protobuf/io/coded_stream.cc`: [1](#0-0) [2](#0-1) 

and documented explicitly in the header: [3](#0-2) 

The comment is explicit: for sizes taken off the wire (string/bytes/submessage lengths), *truncating instead of rejecting out-of-range values would misparse the payload*, so C++ always range-checks the 64-bit parsed value against `INT_MAX` **before** it is ever narrowed/used as an `int`.

The PHP port implements the analogous function, but omits this check entirely: [4](#0-3) 

```php
public function readVarintSizeAsInt(&$var)
{
    if (!$this->readVarint64($var)) {
        return false;
    }
    $var = (int)$var;
    return true;
}
```

The doc comment for this function claims a safety contract ("If the result is larger than the largest integer, `$var` will be -1") that mirrors the C++ semantics, but the implementation does not enforce it: it performs a blind `(int)` cast of whatever `readVarint64()` produced, with no comparison against `INT_MAX` (or any bound) prior to the cast. `readVarint64()` itself, on 32-bit PHP builds, can return values represented as `bcmath` big-number decimal strings (via `GPBUtil::combineInt32ToInt64`/`bcadd`), for the full unsigned 64-bit range: [5](#0-4) 

Casting such an out-of-range numeric string with `(int)` in PHP does not raise an error or reject the value — it silently coerces/truncates to a PHP integer, exactly the "unsafe cast performed before/without a range check" pattern that made the Solidity bug exploitable: the destination-typed value is produced first, and any check that might reject "wire size too large" is simply absent, rather than being run on the original wide value.

This function's output feeds directly into the length used for reading raw bytes and pushing parse limits for both strings/bytes and nested messages: [6](#0-5) 

`readRaw()` does contain a downstream `$size < 0` guard: [7](#0-6) 

and `pushLimit()` independently re-validates bounds: [8](#0-7) 

so on the common 64-bit PHP path (where PHP's native `int` is already 64-bit and the varint value is not represented as a bcmath string), the missing check in `readVarintSizeAsInt` is *largely* masked by these later, independent negative/overflow checks. However, on 32-bit PHP builds — where `readVarint64` can yield an unbounded bcmath decimal string for the upper half of the 64-bit range — the `(int)$var` cast at line 191 is the *only* place the value is coerced into PHP's native integer domain before being handed to `readRaw`/`pushLimit`, and PHP's numeric-string-to-int cast for values outside the platform `int` range is implementation-defined/truncating rather than rejecting. This exactly mirrors the Solidity flaw: the narrowing cast happens with no accompanying validation that the source value actually fits, and the function's own documented contract ("`$var` will be -1" on overflow) is not honored by the code.

### Impact Explanation
If the length value produced by the unchecked cast can end up as a small, non-negative (but incorrect) integer instead of being rejected, the parser would read the wrong number of bytes for a length-delimited field (string/bytes/submessage), causing message content to be misparsed relative to the sender's intent (data corruption / desynchronization of subsequent field parsing), which is the wire-format analog of "loss of funds/state corruption from unchecked truncation" in the original report. This is scoped to the PHP surface (`readVarintSizeAsInt`), reachable only through the public, documented "read size from wire" path used for every string, bytes, and embedded-message field, i.e., a bounded, attacker-controlled Protobuf binary payload parsed by a trusted PHP protobuf consumer application — matching the required consuming-application exposure assumption (Protobuf itself has no RPC endpoint). Severity is Medium: it requires the less-common 32-bit PHP runtime configuration to be the primary way the unchecked path becomes exploitable, and downstream `readRaw`/`pushLimit` checks catch the fully-negative overflow cases, narrowing the exploitable window to specific truncation results rather than an unconditional bypass.

### Likelihood Explanation
Likelihood is limited by two factors I could not fully resolve without running code: (1) most production PHP deployments run 64-bit PHP where `readVarint64` never produces a bcmath string, so the missing check in `readVarintSizeAsInt` has essentially no observable effect there because the value is already a native, correctly-signed 64-bit PHP int and negative overflow is caught by `readRaw`'s `$size < 0` check; (2) I was not able to execute the PHP extension/pure-PHP code paths in this environment to confirm the exact numeric result of `(int)"18446744073709551615"`-style bcmath strings on a 32-bit PHP interpreter, nor to confirm whether the compiled C extension (`php/ext/google/protobuf/...`) — which may be used instead of the pure-PHP implementation shown here — has the same gap. The pure-PHP code path shown is the one indexed and reviewed; the C extension's binary decoder was not located/verified in this pass.

### Recommendation
In `php/src/Google/Protobuf/Internal/CodedInputStream.php::readVarintSizeAsInt`, explicitly validate the 64-bit value against `PHP_INT_MAX` (matching the C++ `ReadVarintSizeAsIntFallback` check against `INT_MAX`) **before** casting, and return `false`/set `-1` per the documented contract when it is out of range, instead of relying on downstream `readRaw`/`pushLimit` checks to incidentally catch bad values:
```php
public function readVarintSizeAsInt(&$var)
{
    if (!$this->readVarint64($var)) {
        return false;
    }
    if (is_string($var) || $var > PHP_INT_MAX || $var < 0) {
        $var = -1;
        return true; // matches documented contract; caller treats -1 as invalid length
    }
    $var = (int)$var;
    return true;
}
```
Also verify the equivalent C-extension decoder path (if separate from this pure-PHP class) enforces the same bound prior to narrowing.

### Proof of Concept
I was not able to execute PHP in this environment to produce a runtime-confirmed proof of concept. A concrete reproduction (to be run by someone with a 32-bit PHP interpreter or `bcmath`-forced code path) would be:
1. Construct a hand-crafted protobuf binary buffer whose length-delimited field tag is followed by a 10-byte varint encoding a value just above `PHP_INT_MAX` (e.g. `0x8000000000000000` or similar), representable only as a bcmath string on a 32-bit build.
2. Call `GPBWire::readString($input, $value)` (or `readMessage`) on this buffer.
3. Instrument/log `readVarintSizeAsInt`'s `$var` immediately before and after `$var = (int)$var;` to confirm that no rejection occurs and that the cast silently coerces the out-of-range value instead of forcing `-1` as the doc comment promises.
This step was not executed; the finding is based on static code reading of `php/src/Google/Protobuf/Internal/CodedInputStream.php` and comparison against the equivalent, correctly-guarded C++ implementation in `src/google/protobuf/io/coded_stream.cc`/`.h`.

### Citations

**File:** src/google/protobuf/io/coded_stream.cc (L540-546)
```text
int CodedInputStream::ReadVarintSizeAsIntSlow() {
  // Directly invoke ReadVarint64Fallback, since we already tried to optimize
  // for one-byte varints.
  std::pair<uint64_t, bool> p = ReadVarint64Fallback();
  if (!p.second || p.first > static_cast<uint64_t>(INT_MAX)) return -1;
  return p.first;
}
```

**File:** src/google/protobuf/io/coded_stream.cc (L548-564)
```text
int CodedInputStream::ReadVarintSizeAsIntFallback() {
  if (BufferSize() >= kMaxVarintBytes ||
      // Optimization:  We're also safe if the buffer is non-empty and it ends
      // with a byte that would terminate a varint.
      (buffer_end_ > buffer_ && !(buffer_end_[-1] & 0x80))) {
    uint64_t temp;
    ::std::pair<bool, const uint8_t*> p = ReadVarint64FromArray(buffer_, &temp);
    if (!p.first || temp > static_cast<uint64_t>(INT_MAX)) return -1;
    buffer_ = p.second;
    return temp;
  } else {
    // Really slow case: we will incur the cost of an extra function call here,
    // but moving this out of line reduces the size of this function, which
    // improves the common case. In micro benchmarks, this is worth about 10-15%
    return ReadVarintSizeAsIntSlow();
  }
}
```

**File:** src/google/protobuf/io/coded_stream.h (L231-240)
```text
  // Reads a varint off the wire into an "int". This should be used for reading
  // sizes off the wire (sizes of strings, submessages, bytes fields, etc).
  //
  // The value from the wire is interpreted as unsigned.  If its value exceeds
  // the representable value of an integer on this platform, instead of
  // truncating we return false. Truncating (as performed by ReadVarint32()
  // above) is an acceptable approach for fields representing an integer, but
  // when we are parsing a size from the wire, truncating the value would result
  // in us misparsing the payload.
  PROTOBUF_FUTURE_ADD_EARLY_NODISCARD bool ReadVarintSizeAsInt(int* value);
```

**File:** php/src/Google/Protobuf/Internal/CodedInputStream.php (L120-156)
```php
    public function readVarint64(&$var)
    {
        $count = 0;

        if (PHP_INT_SIZE == 4) {
            $high = 0;
            $low = 0;
            $b = 0;

            do {
                if ($this->current === $this->buffer_end) {
                    return false;
                }
                if ($count === self::MAX_VARINT_BYTES) {
                    return false;
                }
                $b = ord($this->buffer[$this->current]);
                $bits = 7 * $count;
                if ($bits >= 32) {
                    $high |= (($b & 0x7F) << ($bits - 32));
                } else if ($bits > 25){
                    // $bits is 28 in this case.
                    $low |= (($b & 0x7F) << 28);
                    $high = ($b & 0x7F) >> 4;
                } else {
                    $low |= (($b & 0x7F) << $bits);
                }

                $this->advance(1);
                $count += 1;
            } while ($b & 0x80);

            $var = GPBUtil::combineInt32ToInt64($high, $low);
            if (bccomp($var, 0) < 0) {
                $var = bcadd($var, "18446744073709551616");
            }
        } else {
```

**File:** php/src/Google/Protobuf/Internal/CodedInputStream.php (L181-193)
```php
    /**
     * Read int into $var. If the result is larger than the largest integer, $var
     * will be -1. Advance buffer with consumed bytes.
     * @param $var
     */
    public function readVarintSizeAsInt(&$var)
    {
        if (!$this->readVarint64($var)) {
            return false;
        }
        $var = (int)$var;
        return true;
    }
```

**File:** php/src/Google/Protobuf/Internal/CodedInputStream.php (L255-271)
```php
    public function readRaw($size, &$buffer)
    {
        $current_buffer_size = 0;
        // size (varint) read from the wire could be negative.
        if ($size < 0 || $this->bufferSize() < $size) {
            return false;
        }

        if ($size === 0) {
          $buffer = "";
        } else {
          $buffer = substr($this->buffer, $this->current, $size);
          $this->advance($size);
        }

        return true;
    }
```

**File:** php/src/Google/Protobuf/Internal/CodedInputStream.php (L287-305)
```php
    public function pushLimit($byte_limit)
    {
        // Current position relative to the beginning of the stream.
        $current_position = $this->current();
        $old_limit = $this->current_limit;

        // security: byte_limit is possibly evil, so check for negative values
        // and overflow.
        if ($byte_limit >= 0 &&
            $byte_limit <= PHP_INT_MAX - $current_position &&
            $byte_limit <= $this->current_limit - $current_position) {
            $this->current_limit = $current_position + $byte_limit;
            $this->recomputeBufferLimits();
        } else {
            throw new GPBDecodeException("Fail to push limit.");
        }

        return $old_limit;
    }
```

**File:** php/src/Google/Protobuf/Internal/GPBWire.php (L248-270)
```php
    public static function readString(&$input, &$value)
    {
        $length = 0;
        return $input->readVarintSizeAsInt($length) && $input->readRaw($length, $value);
    }

    public static function readMessage(&$input, &$message)
    {
        $length = 0;
        if (!$input->readVarintSizeAsInt($length)) {
            return false;
        }
        $old_limit = 0;
        $recursion_limit = 0;
        $input->incrementRecursionDepthAndPushLimit(
            $length,
            $old_limit,
            $recursion_limit);
        if ($recursion_limit < 0 || !$message->parseFromStream($input)) {
            return false;
        }
        return $input->decrementRecursionDepthAndPopLimit($old_limit);
    }
```
