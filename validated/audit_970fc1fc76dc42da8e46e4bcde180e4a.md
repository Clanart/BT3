### Title
Silent JSON input truncation via `size_t`→`int` narrowing in `ArrayInputStream` construction for `JsonStringToMessage`/`JsonToBinaryString`/`BinaryToJsonString` - ([File: src/google/protobuf/json/json.cc])

### Summary
The C++ `google::protobuf::json` public entry points that accept an in-memory JSON (or binary) buffer as `absl::string_view`/`std::string` construct an `io::ArrayInputStream` by passing `.size()` (a `size_t`) into a constructor parameter declared as a 32-bit signed `int`, with no length check beforehand. For an over-2 GiB buffer, this narrowing conversion silently truncates or wraps the effective stream length, causing the JSON/binary parser to operate on a buffer size inconsistent with the caller's actual data — mirroring the "32-bit truncation of JSON length" invariant failure described in the BSON report.

### Finding Description
`JsonStringToMessage`, `JsonToBinaryString`, and `BinaryToJsonString` build their `io::ZeroCopyInputStream` like this: [1](#0-0) [2](#0-1) [3](#0-2) 

`ArrayInputStream`'s constructor and iteration logic take/store this as a 32-bit `int`: [4](#0-3) 

`input.size()`/`json_input.size()`/`binary_input.size()` are `size_t`, which on 64-bit builds can legitimately exceed `INT_MAX` (2,147,483,647) for a caller-supplied buffer already resident in memory (e.g., an application that read a large JSON document into a `std::string` and calls `JsonStringToMessage` on it). No range check equivalent to the one used elsewhere in the codebase is performed before this narrowing conversion, unlike `TextFormat::Parser`, which explicitly guards this exact case: [5](#0-4) 

When `size()` is, e.g., `2^32 + N` for small `N`, the truncated `int size_` becomes `N`, and `ArrayInputStream::Next()`/`Skip()` will report EOF after only `N` bytes even though the real buffer is billions of bytes larger — the parser will consider the document complete (or malformed/truncated) after consuming a small fraction of the real input, exactly matching the BSON report's "silently accept only part of the input as a complete document" failure mode. If the truncated value instead becomes negative (bit 31 set), `position_ < size_` is false from the first call, and the stream reports zero bytes immediately, again silently misrepresenting the input rather than reporting an error to the caller.

### Impact Explanation
This is confined to the process embedding protobuf (per the JSON/BSON parsing library exposure assumption) and does not require any privileged access, malicious peer, or hostile schema — only a legitimate, oversized JSON/binary buffer passed to a supported public API (`JsonStringToMessage`, `JsonToBinaryString`, `BinaryToJsonString`). The primary confirmed impact is a silent integrity failure: the parser accepts and successfully parses only a small truncated prefix of the intended document without any error, which can cause the embedding application to act on an incomplete/wrong message (e.g., authorization or configuration JSON silently truncated to a harmless-looking short prefix). Unlike the BSON CVE, I could not establish a code path in this codebase where the truncation leads to an out-of-bounds *read* past the real buffer — `ArrayInputStream::Next()` only ever returns `data_ + position_` for `position_ < size_(truncated)`, which stays within the real allocation, so the heap-over-read component of the original report does not transfer here; only the "misparse/incomplete accept" component transfers.

### Likelihood Explanation
Reaching the vulnerable code requires the caller to already hold or provide a JSON/binary buffer >2 GiB in a single contiguous `std::string`/`string_view`, which is an unusual but not impossible precondition for downstream applications performing bulk ProtoJSON/Any-based ingestion. Given this precondition is exactly the "very large input" precondition specified as the trigger condition in the original BSON report, the analog is directly proportional in likelihood to the original: plausible but requiring an application-supplied multi-gigabyte buffer, which is a narrower attack surface than typical bounded-size wire/JSON parsing bugs.

### Recommendation
Add an explicit size check (mirroring `TextFormat::Parser::CheckParseInputSize`) in `JsonStringToMessage`, `JsonToBinaryString`, and `BinaryToJsonString` in `src/google/protobuf/json/json.cc` before constructing `io::ArrayInputStream`, rejecting inputs whose `size()` exceeds `INT_MAX` with an explicit `absl::Status` error instead of allowing the implicit narrowing conversion.

### Proof of Concept
Not executed (would require allocating and passing a >2 GiB `std::string` to `google::protobuf::json::JsonStringToMessage`, which is impractical to run in this environment). Conceptually:
```cpp
std::string huge_json(size_t{1} << 32 /* 4GiB */ + 100, ' ');
huge_json[0] = '{'; /* ... craft first 100 bytes as valid short JSON object, remainder padding ... */
Message msg;
absl::Status s = google::protobuf::json::JsonStringToMessage(huge_json, &msg);
// Expected (buggy) behavior: ArrayInputStream is constructed with size_ = 100
// (huge_json.size() truncated to int), so the parser only ever sees the first
// 100 bytes and treats the rest as absent, without any size-related error.
```
This reproduction was not executed; verification would require a background Devin session with sufficient memory to allocate a multi-gigabyte buffer and confirm the truncated `ArrayInputStream::size_` value at runtime.

### Citations

**File:** src/google/protobuf/json/json.cc (L51-60)
```text
absl::Status BinaryToJsonString(google::protobuf::util::TypeResolver* resolver,
                                const std::string& type_url,
                                const std::string& binary_input,
                                std::string* json_output,
                                const PrintOptions& options) {
  io::ArrayInputStream input_stream(binary_input.data(), binary_input.size());
  io::StringOutputStream output_stream(json_output);
  return BinaryToJsonStream(resolver, type_url, &input_stream, &output_stream,
                            options);
}
```

**File:** src/google/protobuf/json/json.cc (L79-88)
```text
absl::Status JsonToBinaryString(google::protobuf::util::TypeResolver* resolver,
                                const std::string& type_url,
                                absl::string_view json_input,
                                std::string* binary_output,
                                const ParseOptions& options) {
  io::ArrayInputStream input_stream(json_input.data(), json_input.size());
  io::StringOutputStream output_stream(binary_output);
  return JsonToBinaryStream(resolver, type_url, &input_stream, &output_stream,
                            options);
}
```

**File:** src/google/protobuf/json/json.cc (L114-118)
```text
absl::Status JsonStringToMessage(absl::string_view input, Message* message,
                                 const ParseOptions& options) {
  io::ArrayInputStream input_stream(input.data(), input.size());
  return JsonStreamToMessage(&input_stream, message, options);
}
```

**File:** src/google/protobuf/io/zero_copy_stream_impl_lite.cc (L49-68)
```text
ArrayInputStream::ArrayInputStream(const void* data, int size, int block_size)
    : data_(reinterpret_cast<const uint8_t*>(data)),
      size_(size),
      block_size_(block_size > 0 ? block_size : size),
      position_(0),
      last_returned_size_(0) {}

bool ArrayInputStream::Next(const void** data, int* size) {
  if (position_ < size_) {
    last_returned_size_ = std::min(block_size_, size_ - position_);
    *data = data_ + position_;
    *size = last_returned_size_;
    position_ += last_returned_size_;
    return true;
  } else {
    // We're at the end of the array.
    last_returned_size_ = 0;  // Don't let caller back up.
    return false;
  }
}
```

**File:** src/google/protobuf/text_format.cc (L1972-1983)
```text
template <typename T>
bool TextFormat::Parser::CheckParseInputSize(T& input, Message* output) const {
  if (input.size() > INT_MAX) {
    ReportErrorImpl(-1, 0,
                    absl::StrCat("Input size too large: ",
                                 static_cast<int64_t>(input.size()), " bytes",
                                 " > ", INT_MAX, " bytes."),
                    output->GetDescriptor(), error_collector_);
    return false;
  }
  return true;
}
```
