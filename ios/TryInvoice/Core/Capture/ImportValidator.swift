import CoreGraphics
import Foundation
import ImageIO
import PDFKit

/// 已通过确定性校验的本地待导入文件。
struct ValidatedFile: Equatable, Sendable {
    enum Kind: String, Equatable, Sendable {
        case image
        case pdf
    }

    let kind: Kind
    let ext: String
    let sizeBytes: Int
}

/// 导入前的确定性校验：扩展名、大小与文件头魔数，最后确认图片可解码、PDF 可打开。
/// 只读取文件头，不把整份文件载入内存。
struct ImportValidator {
    static let defaultMaxBytes = 20 * 1024 * 1024

    private static let imageExtensions: Set<String> = ["jpg", "jpeg", "png", "heic"]
    private static let heicBrands: Set<String> = ["heic", "heix", "mif1", "heif"]
    private static let headerByteCount = 12

    let maxBytes: Int

    init(maxBytes: Int = ImportValidator.defaultMaxBytes) {
        self.maxBytes = maxBytes
    }

    func validate(fileAt url: URL, originalName: String) throws -> ValidatedFile {
        let ext = (originalName as NSString).pathExtension.lowercased()
        guard RawVault.allowedExtensions.contains(ext) else {
            throw ImportError.unsupportedType
        }

        let sizeBytes = try fileSize(at: url)
        guard sizeBytes > 0, sizeBytes <= maxBytes else {
            throw ImportError.fileTooLarge
        }

        let kind: ValidatedFile.Kind = Self.imageExtensions.contains(ext) ? .image : .pdf
        let header = try headerBytes(at: url)
        guard Self.matchesMagic(header, kind: kind, ext: ext) else {
            throw ImportError.corruptFile
        }

        switch kind {
        case .image:
            try Self.verifyDecodableImage(at: url)
        case .pdf:
            try Self.verifyReadablePDF(at: url)
        }
        return ValidatedFile(kind: kind, ext: ext, sizeBytes: sizeBytes)
    }

    /// 跟随符号链接读取目标文件大小；缺失或不可读一律按损坏处理。
    private func fileSize(at url: URL) throws -> Int {
        do {
            let target = url.resolvingSymlinksInPath()
            let values = try target.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
            guard values.isRegularFile == true, let size = values.fileSize else {
                throw ImportError.corruptFile
            }
            return size
        } catch let error as ImportError {
            throw error
        } catch {
            throw ImportError.corruptFile
        }
    }

    private func headerBytes(at url: URL) throws -> [UInt8] {
        do {
            let handle = try FileHandle(forReadingFrom: url)
            defer { try? handle.close() }
            guard let data = try handle.read(upToCount: Self.headerByteCount) else {
                throw ImportError.corruptFile
            }
            return [UInt8](data)
        } catch let error as ImportError {
            throw error
        } catch {
            throw ImportError.corruptFile
        }
    }

    private static func matchesMagic(_ header: [UInt8], kind: ValidatedFile.Kind, ext: String) -> Bool {
        switch kind {
        case .image:
            switch ext {
            case "jpg", "jpeg":
                return header.starts(with: [0xFF, 0xD8, 0xFF])
            case "png":
                return header.starts(with: [0x89, 0x50, 0x4E, 0x47])
            case "heic":
                guard header.count >= 12, Array(header[4..<8]) == Array("ftyp".utf8) else {
                    return false
                }
                return heicBrands.contains(String(decoding: header[8..<12], as: UTF8.self))
            default:
                return false
            }
        case .pdf:
            return header.starts(with: Array("%PDF-".utf8))
        }
    }

    /// 帧数声明与容器状态都不足以证明文件可用：截断的 PNG/JPEG 可能报告 1 帧、
    /// 容器也仍为 complete，却只能解出部分帧（首帧状态 unexpectedEOF）。
    /// 因此立即解码首帧，并在解码后确认该帧状态完整。
    private static func verifyDecodableImage(at url: URL) throws {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              CGImageSourceGetCount(source) >= 1 else {
            throw ImportError.corruptFile
        }
        let options = [kCGImageSourceShouldCacheImmediately: true] as CFDictionary
        guard CGImageSourceCreateImageAtIndex(source, 0, options) != nil,
              CGImageSourceGetStatusAtIndex(source, 0) == .statusComplete else {
            throw ImportError.corruptFile
        }
    }

    private static func verifyReadablePDF(at url: URL) throws {
        guard let document = PDFDocument(url: url), !document.isLocked, document.pageCount >= 1 else {
            throw ImportError.corruptFile
        }
    }
}
