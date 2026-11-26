import CoreGraphics
import Foundation
import ImageIO
import PDFKit
import UIKit
import Vision

/// 端侧文字识别入口。实现不得联网，也不得调用云端模型。
protocol OCREngine {
    func recognize(fileAt url: URL, kind: ValidatedFile.Kind) async throws -> OCRDocument
}

/// 基于 Vision 的离线识别引擎：图片单帧识别，PDF 逐页渲染后识别。
struct VisionOCREngine: OCREngine {
    static let defaultMaxPages = 10
    static let defaultRenderScale: CGFloat = 2
    static let recognitionLanguages = ["zh-Hans", "en-US"]

    private let maxPages: Int
    private let renderScale: CGFloat

    init(maxPages: Int = VisionOCREngine.defaultMaxPages, renderScale: CGFloat = VisionOCREngine.defaultRenderScale) {
        self.maxPages = Self.clampedMaxPages(maxPages)
        self.renderScale = max(renderScale, 1)
    }

    /// 注入值只允许在 1…10 内生效：单份 PDF 的处理页数上限不可被调用方突破。
    static func clampedMaxPages(_ value: Int) -> Int {
        min(max(value, 1), defaultMaxPages)
    }

    func recognize(fileAt url: URL, kind: ValidatedFile.Kind) async throws -> OCRDocument {
        do {
            switch kind {
            case .image:
                return try imageDocument(at: url)
            case .pdf:
                return try pdfDocument(at: url)
            }
        } catch let error as ImportError {
            throw error
        } catch {
            throw ImportError.ocrFailed
        }
    }

    private func imageDocument(at url: URL) throws -> OCRDocument {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
            throw ImportError.ocrFailed
        }
        let lines = try recognize(image: image, orientation: Self.orientation(of: source), page: 0)
        return OCRDocument(lines: lines, pageCount: 1, truncated: false)
    }

    private func pdfDocument(at url: URL) throws -> OCRDocument {
        guard let document = PDFDocument(url: url), !document.isLocked, document.pageCount >= 1 else {
            throw ImportError.ocrFailed
        }
        let pageCount = document.pageCount
        let recognizedPages = min(pageCount, maxPages)
        var lines: [OCRLine] = []
        for index in 0..<recognizedPages {
            guard let page = document.page(at: index) else {
                continue
            }
            let bounds = page.bounds(for: .mediaBox)
            let size = CGSize(
                width: max(bounds.width, 1) * renderScale,
                height: max(bounds.height, 1) * renderScale
            )
            guard let image = page.thumbnail(of: size, for: .mediaBox).cgImage else {
                continue
            }
            lines.append(contentsOf: try recognize(image: image, orientation: .up, page: index))
        }
        return OCRDocument(lines: lines, pageCount: pageCount, truncated: pageCount > recognizedPages)
    }

    private func recognize(
        image: CGImage,
        orientation: CGImagePropertyOrientation,
        page: Int
    ) throws -> [OCRLine] {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        request.recognitionLanguages = Self.recognitionLanguages

        let handler = VNImageRequestHandler(cgImage: image, orientation: orientation, options: [:])
        try handler.perform([request])

        return (request.results ?? []).compactMap { observation in
            guard let candidate = observation.topCandidates(1).first else {
                return nil
            }
            return OCRLine(
                text: candidate.string,
                confidence: Double(candidate.confidence),
                box: Self.box(for: observation),
                page: page
            )
        }
    }

    private static func box(for observation: VNRecognizedTextObservation) -> OCRBox {
        let rect = observation.boundingBox
        return OCRBox.fromVisionBoundingBox(
            x: Double(rect.origin.x),
            y: Double(rect.origin.y),
            width: Double(rect.width),
            height: Double(rect.height)
        )
    }

    private static func orientation(of source: CGImageSource) -> CGImagePropertyOrientation {
        guard let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let raw = (properties[kCGImagePropertyOrientation] as? NSNumber)?.uint32Value,
              let orientation = CGImagePropertyOrientation(rawValue: raw) else {
            return .up
        }
        return orientation
    }
}
