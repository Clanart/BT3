Protobuf自体には公開HTTPエンドポイントはないため、この分析は「攻撃者が通常のクライアントとして、境界のあるバイナリ/ProtoJSONを公開パースAPI(`ParseFromString`/`MergeFrom`等)経由で送信し、その結果メッセージがアプリケーションによって`TextFormat`でデバッグ出力・ログ出力される」という消費側アプリケーションの前提で行った。

### Title
Unescaped/unvalidated `Any.type_url` breaks TextFormat bracket-syntax in `PrintAny` - (File: src/google/protobuf/text_format.cc)

### Summary
`aiohttp`の脆弱性は、攻撃者が制御する値(HTTPメソッド)が期待される制限された語彙(GET/POST等)に対してバリデーションされずにプロトコルの構造的位置(リクエスト行)へそのまま挿入され、CRLFなどの特殊文字によって構文が注入・破壊された、というものである [1](#0-0) 。Protobufでの最も近い解析対象は、`google.protobuf.Any`の`type_url`フィールドをTextFormatで展開・整形する`TextFormat::Printer::PrintAny`である。`type_url`はバイナリ/ProtoJSONパースを通じて完全に攻撃者制御可能な文字列であり、その値は`[type_url] { ... }`という構造的なブラケット構文に対しエスケープなしでそのまま書き込まれている [2](#0-1) 。

### Finding Description
`PrintAny`の実装は次の通りである。
```
generator->PrintLiteral("[");
generator->PrintString(type_url);
generator->PrintLiteral("]");
``` [3](#0-2) 

`type_url`は`reflection->GetString(message, type_url_field)`によりメッセージから直接取得された値であり [4](#0-3) 、この値はバイナリ/ProtoJSONの通常のパースAPI経由で攻撃者が任意の文字列を設定できる`bytes`/`string`フィールドである。`any.proto`のドキュメントには、TextFormatでの安全性のために`type_url`は「英数字・パーセントエンコードされたエスケープ・および`/-.~_!$&()*+,;=`の記号のみ」を含むべきと明記されている [5](#0-4) 。しかし、`PrintAny`が呼び出す`internal::GetAnyFieldDescriptors`(実体は`any.cc`内)は、対象メッセージが`google.protobuf.Any`という完全修飾名を持ち、フィールド番号1が`string`型、フィールド番号2が`bytes`型であることのみを検証しており、`type_url`の**文字内容**そのものを検証していない [6](#0-5) 。`internal::ParseAnyTypeUrl`の実装本体はこのチェックアウト内で完全には特定できなかったため、`full_type_name`部分の文字集合バリデーションが実際に行われているかどうかは未確認である(この点は不確実性として明示する)。しかし少なくとも`PrintAny`内で`type_url`全体(スラッシュ以前のプレフィックス含む)がそのまま`PrintString`によってブラケット内に書き込まれており、`]`、改行、`{`、`"`、バックスラッシュ等の特殊文字がエスケープされる形跡はコード上確認できなかった。

一方、Protobufの他のJSON/TextFormat文字列出力経路(例:`src/google/protobuf/json/internal/writer.cc`の`MustEscape`/`WriteEscapedUtf8`、`upb/json/encode.c`の`jsonenc_put_escaped_char`、`csharp/src/Google.Protobuf/JsonFormatter.cs`の`WriteString`、`java/util/.../JsonFormat.java`の`printStringEscapedAndQuoted`)は、通常の文字列フィールド値については制御文字・引用符・HTML危険文字を体系的にエスケープしている [7](#0-6) [8](#0-7) 。つまり、通常の文字列フィールドの出力経路は`aiohttp`修正後と同様に十分な検証・エスケープを備えているのに対し、`Any.type_url`をTextFormatの構造的シンタックスへ挿入する経路だけが、この保護から外れている疑いがある。

### Impact Explanation
`type_url`に`]`や改行、`{`/`}`等を含めることができれば、生成されたTextFormat文字列は`[type_url] { <content> }`という期待された構造を持たず、ブラケットを早期に閉じたり、余分なフィールド様のトークンを注入したりする可能性がある。これは、`aiohttp`のCRLFインジェクションが「攻撃者が制御する値が期待された語彙の外側に出て、リクエストの構造的境界を破壊した」ことと同じ失敗パターンである。影響が実際に及ぶのは、アプリケーションが(a) 攻撃者制御の`Any`メッセージを受理し、(b) それを`SetExpandAny(true)`付きの`TextFormat`で出力し、(c) その出力テキストを何らかの形で再パースまたは信頼済みテキストとして扱う場合(例:デバッグ文字列のラウンドトリップ、ログの再取り込み)に限られる。この経路が実際に存在する場合、整合性(Integrity)への影響がある。

### Likelihood Explanation
この経路が悪用可能であるためには、消費側アプリケーションがユーザー入力由来の`Any`をTextFormatで出力し、かつその出力を再パース・パースツリーとして信頼する、という特定のワークフローが必要である。これは`aiohttp`の脆弱性(攻撃者がHTTPメソッド文字列を直接制御できるだけで即座に影響する)よりも条件が多く、発生確率は中程度〜低めと考えられる。また、`ParseAnyTypeUrl`が`full_type_name`部分に対して何らかの文字集合チェックを行っている可能性があり、その実装を本調査では完全に確認できなかったため、実際の悪用可能性には不確実性が残る。

### Recommendation
`TextFormat::Printer::PrintAny`で`type_url`をブラケット内に書き込む前に、any.protoで規定されている許可文字集合(英数字・`/-.~_!$&()*+,;=`・パーセントエンコード)を強制するバリデーションを追加し、違反時は`PrintAny`をfalseで返して通常のメッセージ表示(quotedかつエスケープされた`type_url`フィールドとしての表示)にフォールバックすることを推奨する。また、`internal::ParseAnyTypeUrl`が本当にこの文字集合を検証しているかをコード全体(未確認部分)で確認し、必要であれば明示的なチェックを追加すべきである。

### Proof of Concept
本調査環境(検索ベースのコードインデックス)ではローカルでのビルド・実行はできず、`internal::ParseAnyTypeUrl`の完全な実装や`BaseTextGenerator::PrintString`のエスケープ挙動を直接確認・実行することができなかった。したがって、以下は**確認されたコードパスに基づく理論的な再現手順**であり、実行結果を主張するものではない:
1. 信頼されたスキーマ上の`Any`メッセージを用意し、バイナリProtobufとして`type_url`フィールドに`"type.googleapis.com/foo.Bar]\n{injected: true"`のような文字列(閉じブラケット`]`と改行を含む)と、有効な`foo.Bar`シリアライズ済み`value`をエンコードする。
2. 通常の公開パースAPI(`Message::ParseFromString`)でこのバイナリを対象アプリケーションのメッセージ型としてパースする。
3. `TextFormat::Printer`で`SetExpandAny(true)`を設定し、`PrintToString`を呼び出す。
4. `text_format.cc:2564-2566`のコードにより`type_url`がエスケープなしで`[`と`]`の間に書き込まれるため、生成されたテキストが意図した`[type_url] { ... }`構造から外れることを、コードリーディングにより確認した(実行によるアサーションは未実施)。

以上より、この分析結果は「有効な可能性のある解析対象」として報告するが、`ParseAnyTypeUrl`の内部実装の未確認部分により完全な立証には至っていないことを明記する。フルソースの確認とビルド・実行による再現には、Devinセッションでのリポジトリ全体へのアクセスが必要である。

### Citations

**File:** src/google/protobuf/text_format.cc (L2527-2574)
```text
bool TextFormat::Printer::PrintAny(const Message& message,
                                   BaseTextGenerator* generator) const {
  const FieldDescriptor* type_url_field;
  const FieldDescriptor* value_field;
  if (!internal::GetAnyFieldDescriptors(message, &type_url_field,
                                        &value_field)) {
    return false;
  }

  const Reflection* reflection = message.GetReflection();

  // Extract the full type name from the type_url field.
  const std::string& type_url = reflection->GetString(message, type_url_field);
  std::string url_prefix;
  std::string full_type_name;

  if (!internal::ParseAnyTypeUrl(type_url, &url_prefix, &full_type_name)) {
    return false;
  }

  // Print the "value" in text.
  const Descriptor* value_descriptor =
      finder_ ? finder_->FindAnyType(message, url_prefix, full_type_name)
              : DefaultFinderFindAnyType(message, url_prefix, full_type_name);
  if (value_descriptor == nullptr) {
    ABSL_LOG(WARNING) << "Can't print proto content: proto type " << type_url
                      << " not found";
    return false;
  }
  DynamicMessageFactory factory;
  std::unique_ptr<Message> value_message(
      factory.GetPrototype(value_descriptor)->New());
  std::string serialized_value = reflection->GetString(message, value_field);
  if (!value_message->ParseFromString(serialized_value)) {
    ABSL_LOG(WARNING) << type_url << ": failed to parse contents";
    return false;
  }
  generator->PrintLiteral("[");
  generator->PrintString(type_url);
  generator->PrintLiteral("]");
  const FastFieldValuePrinter* printer = GetFieldPrinter(value_field);
  printer->PrintMessageStart(message, -1, 0, single_line_mode_, generator);
  generator->Indent();
  Print(*value_message, generator);
  generator->Outdent();
  printer->PrintMessageEnd(message, -1, 0, single_line_mode_, generator);
  return true;
}
```

**File:** src/google/protobuf/any.proto (L90-96)
```text
  // All type URL strings must be legal URI references with the additional
  // restriction (for the text format) that the content of the reference
  // must consist only of alphanumeric characters, percent-encoded escapes, and
  // characters in the following set (not including the outer backticks):
  // `/-.~_!$&()*+,;=`. Despite our allowing percent encodings, implementations
  // should not unescape them to prevent confusion with existing parsers. For
  // example, `type.googleapis.com%2FFoo` should be rejected.
```

**File:** src/google/protobuf/any.cc (L44-59)
```text
bool GetAnyFieldDescriptors(const Message& message,
                            const FieldDescriptor * PROTOBUF_NULLABLE *
                                PROTOBUF_NONNULL type_url_field,
                            const FieldDescriptor * PROTOBUF_NULLABLE *
                                PROTOBUF_NONNULL value_field) {
  const Descriptor* descriptor = message.GetDescriptor();
  if (descriptor->full_name() != kAnyFullTypeName) {
    return false;
  }
  *type_url_field = descriptor->FindFieldByNumber(1);
  *value_field = descriptor->FindFieldByNumber(2);
  return (*type_url_field != nullptr &&
          (*type_url_field)->type() == FieldDescriptor::TYPE_STRING &&
          *value_field != nullptr &&
          (*value_field)->type() == FieldDescriptor::TYPE_BYTES);
}
```

**File:** src/google/protobuf/json/internal/writer.cc (L194-269)
```text
// Decides whether we must escape `scalar`.
//
// If the given Unicode scalar would not use a \u escape, `custom_escape` will
// be set to a non-empty string.
static bool MustEscape(uint32_t scalar, absl::string_view& custom_escape) {
  switch (scalar) {
    // These escapes are defined by the JSON spec. We do not escape /.
    case '\n':
      custom_escape = R"(\n)";
      return true;
    case '\r':
      custom_escape = R"(\r)";
      return true;
    case '\t':
      custom_escape = R"(\t)";
      return true;
    case '\"':
      custom_escape = R"(\")";
      return true;
    case '\f':
      custom_escape = R"(\f)";
      return true;
    case '\b':
      custom_escape = R"(\b)";
      return true;
    case '\\':
      custom_escape = R"(\\)";
      return true;

    case kErrorSentinel:
      // Decoding failure turns into spaces, *not* replacement characters. We
      // handle this separately from "normal" spaces so that it follows the
      // escaping code-path.
      //
      // Note that literal replacement characters in the input string DO NOT
      // get turned into spaces; this is only for decoding failures!
      custom_escape = " ";
      return true;

    // These are not required by the JSON spec, but help
    // to prevent security bugs in JavaScript.
    //
    // These were originally present in the ESF parser, so they are kept for
    // legacy compatibility (and because escaping most of these is in good
    // taste, regardless).
    case '<':
    case '>':
    case 0xfeff:      // Zero width no-break space.
    case 0xfff9:      // Interlinear annotation anchor.
    case 0xfffa:      // Interlinear annotation separator.
    case 0xfffb:      // Interlinear annotation terminator.
    case 0x00ad:      // Soft-hyphen.
    case 0x06dd:      // Arabic end of ayah.
    case 0x070f:      // Syriac abbreviation mark.
    case 0x17b4:      // Khmer vowel inherent Aq.
    case 0x17b5:      // Khmer vowel inherent Aa.
    case 0x000e0001:  // Language tag.
      return true;
    default:
      static constexpr std::pair<uint32_t, uint32_t> kEscapedRanges[] = {
          {0x0000, 0x001f},          // ASCII control.
          {0x007f, 0x009f},          // High ASCII bytes.
          {0x0600, 0x0603},          // Arabic signs.
          {0x200b, 0x200f},          // Zero width etc.
          {0x2028, 0x202e},          // Separators etc.
          {0x2060, 0x2064},          // Invisible etc.
          {0x206a, 0x206f},          // Shaping etc.
          {0x0001d173, 0x0001d17a},  // Music formatting.
          {0x000e0020, 0x000e007f},  // TAG symbols.
      };

      return absl::c_any_of(kEscapedRanges, [scalar](auto range) {
        return range.first <= scalar && scalar <= range.second;
      });
  }
}
```

**File:** upb/json/encode.c (L268-301)
```c
static void jsonenc_put_escaped_char(jsonenc* e, char ch) {
  switch (ch) {
    case '\n':
      jsonenc_putstr(e, "\\n");
      break;
    case '\r':
      jsonenc_putstr(e, "\\r");
      break;
    case '\t':
      jsonenc_putstr(e, "\\t");
      break;
    case '\"':
      jsonenc_putstr(e, "\\\"");
      break;
    case '\f':
      jsonenc_putstr(e, "\\f");
      break;
    case '\b':
      jsonenc_putstr(e, "\\b");
      break;
    case '\\':
      jsonenc_putstr(e, "\\\\");
      break;
    default:
      if ((uint8_t)ch < 0x20) {
        jsonenc_printf(e, "\\u%04x", (int)(uint8_t)ch);
      } else {
        /* This could be a non-ASCII byte.  We rely on the string being valid
         * UTF-8. */
        jsonenc_putbytes(e, &ch, 1);
      }
      break;
  }
}
```
