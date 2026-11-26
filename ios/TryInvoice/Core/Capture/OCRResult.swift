import Foundation

/// 归一化文字框：左上原点，坐标与宽高均在 0…1。
struct OCRBox: Equatable, Sendable {
    let x: Double
    let y: Double
    let width: Double
    let height: Double

    /// 任何来源的坐标在构造时即钳到 0…1，非有限值归零，
    /// 保证结果不依赖调用方先做清洗（越界/NaN 不得流入 JSON）。
    init(x: Double, y: Double, width: Double, height: Double) {
        self.x = Self.clamp(x)
        self.y = Self.clamp(y)
        self.width = Self.clamp(width)
        self.height = Self.clamp(height)
    }

    /// Vision 的 boundingBox 以左下角为原点，这里翻转为左上原点并钳到 0…1。
    static func fromVisionBoundingBox(
        x: Double,
        y: Double,
        width: Double,
        height: Double
    ) -> OCRBox {
        let boxHeight = clamp(height)
        return OCRBox(
            x: x,
            y: 1 - clamp(y) - boxHeight,
            width: width,
            height: boxHeight
        )
    }

    /// 非有限值与越界值都归一到 0…1，保证下游 JSON 契约稳定。
    static func clamp(_ value: Double) -> Double {
        guard value.isFinite else {
            return 0
        }
        return min(max(value, 0), 1)
    }
}

struct OCRLine: Equatable, Sendable {
    let text: String
    let confidence: Double
    let box: OCRBox
    let page: Int
}

/// 端侧 OCR 结果。初始化时即完成规范化，保证同一输入始终得到同一输出。
struct OCRDocument: Equatable, Sendable {
    static let engineName = "vision"
    static let levelName = "accurate"

    let lines: [OCRLine]
    let pageCount: Int
    let truncated: Bool

    init(lines: [OCRLine], pageCount: Int, truncated: Bool) {
        self.lines = Self.normalize(lines)
        self.pageCount = max(pageCount, 0)
        self.truncated = truncated
    }

    /// 丢弃空白文本、钳制置信度与坐标，并按 page → y → x 稳定排序。
    static func normalize(_ lines: [OCRLine]) -> [OCRLine] {
        let indexed = lines.enumerated().compactMap { offset, line -> (offset: Int, line: OCRLine)? in
            let text = line.text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else {
                return nil
            }
            return (offset, OCRLine(
                text: text,
                confidence: OCRBox.clamp(line.confidence),
                box: line.box,
                page: max(line.page, 0)
            ))
        }
        return indexed.sorted { left, right in
            if left.line.page != right.line.page {
                return left.line.page < right.line.page
            }
            if left.line.box.y != right.line.box.y {
                return left.line.box.y < right.line.box.y
            }
            if left.line.box.x != right.line.box.x {
                return left.line.box.x < right.line.box.x
            }
            return left.offset < right.offset
        }.map(\.line)
    }

    /// 与后端 `_ocr_lines` 兼容的 `lines[].text/confidence` 契约，附带坐标与页码。
    func asJSONValue() -> JSONValue {
        .object([
            "engine": .string(Self.engineName),
            "level": .string(Self.levelName),
            "pages": .integer(pageCount),
            "truncated": .boolean(truncated),
            "lines": .array(lines.map(Self.jsonValue(for:))),
        ])
    }

    private static func jsonValue(for line: OCRLine) -> JSONValue {
        .object([
            "text": .string(line.text),
            "confidence": .number(line.confidence),
            "box": .object([
                "x": .number(line.box.x),
                "y": .number(line.box.y),
                "w": .number(line.box.width),
                "h": .number(line.box.height),
            ]),
            "page": .integer(line.page),
        ])
    }
}
