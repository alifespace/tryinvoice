import SwiftUI
import UIKit

/// 交易详情：只编辑并回传用户改动字段，金额、均分与小费规则全部由服务端负责。
struct TransactionDetailView: View {
    private enum DocRole: String, CaseIterable, Identifiable {
        case invoice
        case receipt

        var id: String { rawValue }
        var title: String { self == .invoice ? "Invoice" : "Receipt" }
    }

    private struct LinkOption: Identifiable {
        let id: String
        let name: String
    }

    private let transaction: TransactionOut
    private let images: any DocumentImageSource
    private let candidates: [LinkCandidate]
    private let onSave: (TransactionPatch) async -> String?

    @Environment(\.dismiss) private var dismiss
    @State private var role: DocRole = .invoice
    @State private var image: UIImage?
    @State private var imageFailed = false
    @State private var category: String
    @State private var splitCount: Int
    @State private var tip: String
    @State private var notes: String
    @State private var invoiceDocId: String?
    @State private var receiptDocId: String?
    @State private var isSaving = false
    @State private var saveError: String?

    init(
        transaction: TransactionOut,
        candidates: [LinkCandidate],
        images: any DocumentImageSource,
        onSave: @escaping (TransactionPatch) async -> String?
    ) {
        self.transaction = transaction
        self.candidates = candidates
        self.images = images
        self.onSave = onSave
        _category = State(initialValue: transaction.category)
        _splitCount = State(initialValue: transaction.splitCount)
        _tip = State(initialValue: transaction.tipAmount)
        _notes = State(initialValue: transaction.notes)
        _invoiceDocId = State(initialValue: Self.documentId(of: "invoice", in: transaction))
        _receiptDocId = State(initialValue: Self.documentId(of: "receipt", in: transaction))
    }

    var body: some View {
        Form {
            Section("凭证") {
                Picker("凭证类型", selection: $role) {
                    ForEach(DocRole.allCases) { item in
                        Text(item.title).tag(item)
                    }
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("detail.docPicker")

                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                } else if imageFailed {
                    Text("图片暂不可用")
                        .foregroundStyle(.secondary)
                        .accessibilityIdentifier("detail.imageUnavailable")
                } else {
                    ProgressView()
                }
            }

            Section("服务端结果") {
                LabeledContent("商家", value: transaction.merchant)
                LabeledContent("报销金额", value: transaction.claimAmount)
            }

            Section("修订") {
                TextField("分类", text: $category)
                    .accessibilityIdentifier("detail.category")
                Stepper("均分人数 \(splitCount)", value: $splitCount, in: 1...99)
                    .accessibilityIdentifier("detail.splitCount")
                TextField("小费", text: $tip)
                    .keyboardType(.decimalPad)
                    .accessibilityIdentifier("detail.tip")
                TextField("备注", text: $notes)
                    .accessibilityIdentifier("detail.notes")
                documentMenu(title: "关联 Invoice", docRole: .invoice, selection: $invoiceDocId)
                documentMenu(title: "关联 Receipt", docRole: .receipt, selection: $receiptDocId)
            }

            Section {
                Button("保存") {
                    Task { await save() }
                }
                .disabled(isSaving)
                .accessibilityIdentifier("detail.save")
            }
        }
        .navigationTitle("交易详情")
        .errorBanner(message: saveError, identifier: "detail.error")
        .task(id: role) { await loadImage() }
    }

    private static func documentId(of role: String, in transaction: TransactionOut) -> String? {
        transaction.documents.first { $0.role == role }?.id
    }

    /// 选项只包含当前关联与本地候选；角色/占用校验交给服务端，视图不筛除。
    private func documentMenu(
        title: String,
        docRole: DocRole,
        selection: Binding<String?>
    ) -> some View {
        let currentId = Self.documentId(of: docRole.rawValue, in: transaction)
        let options = linkOptions(currentId: currentId)
        return Menu {
            ForEach(options) { option in
                Button(option.name) { selection.wrappedValue = option.id }
            }
        } label: {
            HStack {
                Text(title)
                Spacer()
                Text(optionName(selection.wrappedValue) ?? "未关联")
                    .foregroundStyle(.secondary)
            }
        }
        .accessibilityIdentifier("detail.link.\(docRole.rawValue)")
    }

    private func linkOptions(currentId: String?) -> [LinkOption] {
        var options: [LinkOption] = []
        if let currentId, let name = optionName(currentId) {
            options.append(LinkOption(id: currentId, name: name))
        }
        for candidate in candidates {
            if let currentId, UUID(uuidString: currentId) == candidate.id {
                continue
            }
            options.append(LinkOption(id: candidate.id.uuidString, name: candidate.name))
        }
        return options
    }

    private func optionName(_ id: String?) -> String? {
        guard let id else { return nil }
        if let identifier = UUID(uuidString: id),
           let candidate = candidates.first(where: { $0.id == identifier }) {
            return candidate.name
        }
        return transaction.documents.first { $0.id == id }?.originalFilename
    }

    private func loadImage() async {
        image = nil
        imageFailed = false
        let documentId = role == .invoice ? invoiceDocId : receiptDocId
        guard let documentId else {
            imageFailed = true
            return
        }
        do {
            let data = try await images.imageData(forDocumentId: documentId)
            image = UIImage(data: data)
            imageFailed = image == nil
        } catch {
            imageFailed = true
        }
    }

    /// 只回传发生变化且有值的字段，其余保持 nil 交给服务端沿用原值；nil 不做清除。
    private func save() async {
        guard !isSaving else { return }
        isSaving = true
        defer { isSaving = false }

        let currentInvoice = Self.documentId(of: "invoice", in: transaction)
        let currentReceipt = Self.documentId(of: "receipt", in: transaction)
        let trimmedCategory = category.trimmingCharacters(in: .whitespacesAndNewlines)
        let patch = TransactionPatch(
            category: diff(trimmedCategory, transaction.category),
            invoiceDocId: invoiceDocId != currentInvoice ? invoiceDocId : nil,
            notes: diff(notes, transaction.notes),
            receiptDocId: receiptDocId != currentReceipt ? receiptDocId : nil,
            splitCount: diff(splitCount, transaction.splitCount),
            tipAmount: diff(tip, transaction.tipAmount).map { JSONValue.string($0) }
        )
        guard patch.hasChange else { return }
        if let error = await onSave(patch) {
            saveError = error
        } else {
            saveError = nil
            dismiss()
        }
    }

    private func diff<Value: Equatable>(_ new: Value, _ old: Value) -> Value? {
        new == old ? nil : new
    }
}

private extension TransactionPatch {
    /// 六个可编辑字段是否至少有一个显式改动。
    var hasChange: Bool {
        category != nil
            || invoiceDocId != nil
            || notes != nil
            || receiptDocId != nil
            || splitCount != nil
            || tipAmount != nil
    }
}
