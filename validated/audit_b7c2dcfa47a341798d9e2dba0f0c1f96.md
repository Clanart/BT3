## Analog Identified: Parser error messages leak `debug_redact`-marked field values that the Printer path explicitly protects

### Title
Sensitive fields marked `debug_redact` leak their raw values through `JsonFormat`/`TextFormat` parse-error exception messages - (File: `java/util/src/main/java/com/google/protobuf/util/JsonFormat.java`)

### Summary
Protobuf provides `debug_redact` as the explicit mechanism to keep sensitive field values (passwords, tokens, etc.) out of human-readable/debug output [1](#0-0) . The `TextFormat.Printer` code path checks this flag and substitutes a redaction marker before emitting field values [2](#0-1) [3](#0-2) . However, the *parsing* error paths in `JsonFormat.ParserImpl` (and the analogous `TextFormat` tokenizer/parser) never consult `debug_redact`/`getRedactionState()` before embedding the raw attacker/caller-supplied value into the thrown exception's message string.

### Finding Description
When `JsonFormat.parser().merge(json, builder)` encounters a type mismatch or invalid value for a field, it throws `InvalidProtocolBufferException` whose message directly concatenates the raw JSON value:
- `parseInt32`/`parseInt64`/`parseUint32`/`parseUint64`: `"Not an int32 value: " + json`, `"Out of range uint32 value: " + json` [4](#0-3) 
- `parseEnum`: `"Invalid enum value: " + json + " for enum type: " + ...` [5](#0-4) 
- `parseFieldValue`: `"Invalid value: " + json + " for expected type: " + field.getType()` [6](#0-5) 
- `mergeTimestamp`/`mergeDuration`: `"Failed to parse timestamp: " + json`, `"Failed to parse duration: " + json` [7](#0-6) 

None of these code paths check `field.getRedactionState()` / `getDebugRedact()`, the exact API that `TextFormat.Printer.TryRedactFieldValue` uses to suppress sensitive values [8](#0-7) . A grep across `JsonFormat.java` and the C++ `src/google/protobuf/json/` implementation confirms zero references to `debug_redact`/redaction anywhere in the parsing code, versus multiple explicit checks in the printer code. The same gap exists in `TextFormat`'s parser: `integerParseException`/`floatParseException` embed `NumberFormatException` messages that themselves contain the raw offending token text [9](#0-8) , and `Tokenizer.parseException` includes the raw current token verbatim, e.g. `"Invalid field value: " + tokenizer.currentToken` [10](#0-9) , with no redaction check either.

The C# implementation shows the identical pattern: `JsonParser.ParseNumericString`/`MergeDuration` embed the raw text into `InvalidProtocolBufferException` messages unconditionally [11](#0-10) [12](#0-11) .

### Impact Explanation
This directly parallels CVE-2020-1698: an exception message intended for debugging/diagnostics leaks a value that the schema author explicitly flagged as sensitive via `debug_redact`. Consuming applications routinely log caught `InvalidProtocolBufferException`/`TextFormat.ParseException` messages (e.g., in request-validation error handlers, API gateways). If a client sends a malformed value for a field the application owner marked `debug_redact = true` (a proto2/proto3 field-level annotation specifically meant to prevent secrets from appearing in logs/debug strings), the raw secret value is written into application logs via the exception's `getMessage()`/`toString()`, defeating the purpose of the annotation. This is a confidentiality-only issue (CWE-200/CWE-532 analog), matching the CVSS profile of the original report (`C:H/I:N/A:N`).

### Likelihood Explanation
Likelihood is moderate-to-high in any application that (a) uses `debug_redact` on sensitive fields, (b) accepts ProtoJSON or TextFormat input from a supported public parse API (`JsonFormat.parser().merge(...)`, `TextFormat.merge(...)`), and (c) logs parse exceptions on failure — a very common pattern for input validation errors. No privileged access or malicious schema is required; a well-formed schema with a `debug_redact` field and an ordinary malformed request (e.g., sending a string where an int32 is expected for the redacted field, or an out-of-range/malformed value) is sufficient to trigger the leak on the very next line the client (mis)supplies.

### Recommendation
Extend the existing redaction check (`FieldDescriptor.getRedactionState()` in Java, `TextFormat::GetRedactionState` in C++) into the parser/error-construction paths of `JsonFormat.ParserImpl` (`parseInt32`, `parseInt64`, `parseUint32`, `parseUint64`, `parseEnum`, `parseFieldValue`, `mergeTimestamp`, `mergeDuration`) and `TextFormat.Tokenizer`/`Parser` error builders, so that when the field being parsed is marked `debug_redact`, the exception message substitutes a redaction marker (consistent with `REDACTED_MARKER`/`kFieldValueReplacement` used by the printer) instead of the raw value. Apply the same fix to the C# `JsonParser` and any other language bindings that build error strings from field values.

### Proof of Concept
1. Define a proto message with a sensitive field: `string password = 1 [debug_redact = true];` (as already exercised by `optional_redacted_string` in `rust/test/unittest.proto` [13](#0-12) , same annotation applies to Java/C++/C#).
2. Using `JsonFormat.parser().merge(...)`, send JSON `{"password": {}}` (an object where a string is expected) or a malformed enum/int value for that field.
3. `parseFieldValue` throws `InvalidProtocolBufferException("Invalid value: {} for expected type: STRING")` — but if the caller supplies `{"password": "S3cr3tValue"}` against, e.g., an `int32` redacted field expecting a number, the thrown message is `"Not an int32 value: \"S3cr3tValue\""`, which embeds the raw secret string verbatim into the exception, per [4](#0-3) .
4. Any application code that logs `e.getMessage()` on catching this `InvalidProtocolBufferException` (a standard error-handling pattern) will persist the secret value in logs, despite the schema explicitly marking the field `debug_redact = true` for exactly this purpose.

### Citations

**File:** src/google/protobuf/descriptor.proto (L776-778)
```text
  // Indicate that the field value should not be printed out when using debug
  // formats, e.g. when the field contains sensitive credentials.
  optional bool debug_redact = 16 [default = false];
```

**File:** src/google/protobuf/text_format.cc (L3279-3305)
```text
bool TextFormat::Printer::TryRedactFieldValue(
    const Message& message, const FieldDescriptor* field,
    BaseTextGenerator* generator, bool insert_value_separator) const {
  TextFormat::RedactionState redaction_state =
      DescriptorPool::MemoizeProjection(
          field, [](const FieldDescriptor* field) {
            return TextFormat::GetRedactionState(field);
          });
  if (redaction_state.redact) {
    if (redact_debug_string_) {
      IncrementRedactedFieldCounter();
      if (insert_value_separator) {
        generator->PrintMaybeWithMarker(MarkerToken(), ": ");
      }
      generator->PrintString(kFieldValueReplacement);
      if (insert_value_separator) {
        if (single_line_mode_) {
          generator->PrintLiteral(" ");
        } else {
          generator->PrintLiteral("\n");
        }
      }
      return true;
    }
  }
  return false;
}
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L673-676)
```java
    private boolean shouldRedact(final FieldDescriptor field, TextGenerator generator) {
      FieldDescriptor.RedactionState state = field.getRedactionState();
      return enablingSafeDebugFormat && state.redact;
    }
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L1692-1706)
```java
    /**
     * Constructs an appropriate {@link ParseException} for the given {@code NumberFormatException}
     * when trying to parse an integer.
     */
    private ParseException integerParseException(final NumberFormatException e) {
      return parseException("Couldn't parse integer: " + e.getMessage());
    }

    /**
     * Constructs an appropriate {@link ParseException} for the given {@code NumberFormatException}
     * when trying to parse a float or double.
     */
    private ParseException floatParseException(final NumberFormatException e) {
      return parseException("Couldn't parse number: " + e.getMessage());
    }
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L2653-2662)
```java
    /** Skips a field value. */
    private void skipFieldValue(Tokenizer tokenizer) throws ParseException {
      if (!tokenizer.tryConsumeByteString()
          && !tokenizer.tryConsumeIdentifier() // includes enum & boolean
          && !tokenizer.tryConsumeInt64() // includes int32
          && !tokenizer.tryConsumeUInt64() // includes uint32
          && !tokenizer.tryConsumeDouble()
          && !tokenizer.tryConsumeFloat()) {
        throw tokenizer.parseException("Invalid field value: " + tokenizer.currentToken);
      }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1945-1968)
```java
    private void mergeTimestamp(JsonElement json, Message.Builder builder)
        throws InvalidProtocolBufferException {
      try {
        Timestamp value = Timestamps.parse(json.getAsString());
        builder.mergeFrom(value.toByteString());
      } catch (ParseException | UnsupportedOperationException e) {
        InvalidProtocolBufferException ex =
            new InvalidProtocolBufferException("Failed to parse timestamp: " + json);
        ex.initCause(e);
        throw ex;
      }
    }

    private void mergeDuration(JsonElement json, Message.Builder builder)
        throws InvalidProtocolBufferException {
      try {
        Duration value = Durations.parse(json.getAsString());
        builder.mergeFrom(value.toByteString());
      } catch (ParseException | UnsupportedOperationException e) {
        InvalidProtocolBufferException ex =
            new InvalidProtocolBufferException("Failed to parse duration: " + json);
        ex.initCause(e);
        throw ex;
      }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L2235-2253)
```java
    private int parseInt32(JsonElement json) throws InvalidProtocolBufferException {
      try {
        return Integer.parseInt(json.getAsString());
      } catch (RuntimeException e) {
        // Fall through.
      }
      // JSON doesn't distinguish between integer values and floating point values so "1" and
      // "1.000" are treated as equal in JSON. For this reason we accept floating point values for
      // integer fields as well as long as it actually is an integer (i.e., round(value) == value).
      try {
        BigDecimal value = parseBigDecimal(json.getAsString());
        return value.intValueExact();
      } catch (RuntimeException e) {
        InvalidProtocolBufferException ex =
            new InvalidProtocolBufferException("Not an int32 value: " + json);
        ex.initCause(e);
        throw ex;
      }
    }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L2437-2453)
```java
    @Nullable
    private EnumValueDescriptor parseEnum(EnumDescriptor enumDescriptor, JsonElement json)
        throws InvalidProtocolBufferException {
      // Calling json.getAsString() works for both JsonPrimitive and single-element JsonArray (e.g.,
      // ["FOO"] or [2]), throwing UnsupportedOperationException/IllegalStateException for other
      // structures.
      final String name;
      try {
        name = json.getAsString();
      } catch (UnsupportedOperationException | IllegalStateException e) {
        // UnsupportedOperationException is thrown by Gson when getAsString() is called on a
        // JsonObject (e.g., "{}") or JsonNull (e.g., "null"). IllegalStateException is thrown when
        // getAsString() is called on a JsonArray whose length is not exactly 1 (e.g., "[]" or
        // '["FOO", "BAR"]').
        throw new InvalidProtocolBufferException(
            "Invalid enum value: " + json + " for enum type: " + enumDescriptor.getFullName(), e);
      }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L2509-2516)
```java
      } else if (json instanceof JsonObject) {
        if (field.getType() != FieldDescriptor.Type.MESSAGE
            && field.getType() != FieldDescriptor.Type.GROUP) {
          // If the field type is primitive, but the json type is JsonObject rather than
          // JsonElement, throw a type mismatch error.
          throw new InvalidProtocolBufferException(
              "Invalid value: " + json + " for expected type: " + field.getType());
        }
```

**File:** csharp/src/Google.Protobuf/JsonParser.cs (L826-861)
```csharp
        private static T ParseNumericString<T>(string text, Func<string, NumberStyles, IFormatProvider, T> parser)
        {
            // Can't prohibit this with NumberStyles.
            if (text.StartsWith("+"))
            {
                throw new InvalidProtocolBufferException($"Invalid numeric value: {text}");
            }
            if (text.StartsWith("0") && text.Length > 1)
            {
                if (text[1] >= '0' && text[1] <= '9')
                {
                    throw new InvalidProtocolBufferException($"Invalid numeric value: {text}");
                }
            }
            else if (text.StartsWith("-0") && text.Length > 2)
            {
                if (text[2] >= '0' && text[2] <= '9')
                {
                    throw new InvalidProtocolBufferException($"Invalid numeric value: {text}");
                }
            }
            try
            {
                return parser(text, NumberStyles.AllowLeadingSign | NumberStyles.AllowDecimalPoint | NumberStyles.AllowExponent, CultureInfo.InvariantCulture);
            }
            catch (FormatException)
            {
                throw new InvalidProtocolBufferException($"Invalid numeric value for type: {text}");
            }
            catch (OverflowException)
            {
                throw new InvalidProtocolBufferException($"Value out of range: {text}");
            }
        }

        /// <summary>
```

**File:** csharp/src/Google.Protobuf/JsonParser.cs (L952-994)
```csharp
        private static void MergeDuration(IMessage message, JsonToken token)
        {
            if (token.Type != JsonToken.TokenType.StringValue)
            {
                throw new InvalidProtocolBufferException("Expected string value for Duration");
            }
            var match = DurationRegex.Match(token.StringValue);
            if (!match.Success)
            {
                throw new InvalidProtocolBufferException("Invalid Duration value: " + token.StringValue);
            }
            var sign = match.Groups["sign"].Value;
            var secondsText = match.Groups["int"].Value;
            // Prohibit leading insignficant zeroes
            if (secondsText[0] == '0' && secondsText.Length > 1)
            {
                throw new InvalidProtocolBufferException("Invalid Duration value: " + token.StringValue);
            }
            var subseconds = match.Groups["subseconds"].Value;
            var multiplier = sign == "-" ? -1 : 1;

            try
            {
                long seconds = long.Parse(secondsText, CultureInfo.InvariantCulture) * multiplier;
                int nanos = 0;
                if (subseconds.Length != 0)
                {
                    // This should always work, as we've got 1-9 digits.
                    int parsedFraction = int.Parse(subseconds.Substring(1));
                    nanos = parsedFraction * SubsecondScalingFactors[subseconds.Length] * multiplier;
                }
                if (!Duration.IsNormalized(seconds, nanos))
                {
                    throw new InvalidProtocolBufferException($"Invalid Duration value: {token.StringValue}");
                }
                message.Descriptor.Fields[Duration.SecondsFieldNumber].Accessor.SetValue(message, seconds);
                message.Descriptor.Fields[Duration.NanosFieldNumber].Accessor.SetValue(message, nanos);
            }
            catch (FormatException)
            {
                throw new InvalidProtocolBufferException($"Invalid Duration value: {token.StringValue}");
            }
        }
```

**File:** rust/test/unittest.proto (L1801-1801)
```text
  string optional_redacted_string = 1 [debug_redact = true];
```
