#if DEBUG
import Foundation

/// UI 测试专用合成复核数据；不含真实商家、姓名、卡号或发票原文。
actor UITestReviewAPI: TransactionReviewAPI {
    static let batchId = UUID(uuidString: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa") ?? UUID()
    static let flaggedTransactionId = "11111111-1111-1111-1111-111111111111"
    static let batchTitle = "合成批次"

    /// 桩内只保存可编辑的展示字段，输出时补齐全量字段；金额保持固定合成值。
    private struct Synth {
        let id: String
        let merchant: String
        let flags: [String]
        var category: String
        var notes: String
        var splitCount: Int
        var tipAmount: String
        var invoiceDocId: String? = "doc-invoice-1"
        var receiptDocId: String? = "doc-receipt-1"

        var documents: [TransactionDocumentOut] {
            var items: [TransactionDocumentOut] = []
            if let invoiceDocId {
                items.append(TransactionDocumentOut(id: invoiceDocId, originalFilename: "invoice.png", role: "invoice"))
            }
            if let receiptDocId {
                items.append(TransactionDocumentOut(id: receiptDocId, originalFilename: "receipt.png", role: "receipt"))
            }
            return items
        }

        var output: TransactionOut {
            TransactionOut(
                airportDirection: nil,
                airportIata: nil,
                category: category,
                claimAmount: "12.50",
                currency: "USD",
                documents: documents,
                flags: flags,
                id: id,
                isUserVerified: false,
                merchant: merchant,
                notes: notes,
                originalAmount: "12.50",
                paymentStatus: "paid",
                regionDetail: "旧金山",
                regionScope: "in_scope",
                splitCount: splitCount,
                tipAmount: tipAmount,
                txDate: "2026-11-20"
            )
        }
    }

    private var items: [Synth]

    init() {
        items = [
            Synth(
                id: Self.flaggedTransactionId,
                merchant: "合成小馆",
                flags: ["tip_excluded"],
                category: "餐饮",
                notes: "原始备注",
                splitCount: 1,
                tipAmount: "0.00"
            ),
            Synth(
                id: "22222222-2222-2222-2222-222222222222",
                merchant: "合成出行",
                flags: [],
                category: "交通",
                notes: "",
                splitCount: 1,
                tipAmount: "0.00"
            ),
        ]
    }

    func listTransactions(batchId: UUID) async throws -> TransactionListOut {
        TransactionListOut(
            batchId: Self.batchId.uuidString,
            groups: ["餐饮", "交通"].map { category in
                CategoryGroup(
                    category: category,
                    scopes: [
                        ScopeGroup(
                            regionScope: "in_scope",
                            regions: [
                                RegionGroup(
                                    regionDetail: "旧金山",
                                    transactions: items
                                        .filter { $0.category == category }
                                        .map(\.output)
                                ),
                            ]
                        ),
                    ]
                )
            },
            missingAlerts: [.string("缺少 Invoice 关联")],
            status: "needs_review"
        )
    }

    /// 仅回写 patch 中显式给出的展示字段；金额字段始终保留合成原值。
    func updateTransaction(id: UUID, _ payload: TransactionPatch) async throws -> TransactionOut {
        guard let index = items.firstIndex(where: { UUID(uuidString: $0.id) == id }) else {
            throw AppError.validation
        }
        if payload.notes == "FAIL" {
            throw AppError.validation
        }
        if let category = payload.category {
            items[index].category = category
        }
        if let notes = payload.notes {
            items[index].notes = notes
        }
        if let splitCount = payload.splitCount {
            items[index].splitCount = splitCount
        }
        if let invoiceDocId = payload.invoiceDocId {
            items[index].invoiceDocId = invoiceDocId
        }
        if let receiptDocId = payload.receiptDocId {
            items[index].receiptDocId = receiptDocId
        }
        if case let .string(tip)? = payload.tipAmount {
            items[index].tipAmount = tip
        }
        return items[index].output
    }
}

/// UI 测试占位候选：固定 1 个合成文档，名称不含真实票据信息。
struct UITestLinkCandidateSource: LinkCandidateSource {
    static let candidateId = UUID(uuidString: "cccccccc-cccc-cccc-cccc-cccccccccccc") ?? UUID()
    static let candidateName = "synthetic-extra.pdf"

    func candidates(forBatchId id: UUID) async -> [LinkCandidate] {
        [LinkCandidate(id: Self.candidateId, name: Self.candidateName)]
    }
}

/// 复核界面只读的 1×1 PNG，用于 UI 测试占位；不读取真实票据。
struct UITestImageSource: DocumentImageSource {
    private static let pngBase64 =
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

    func imageData(forDocumentId id: String) async throws -> Data {
        guard let data = Data(base64Encoded: Self.pngBase64) else {
            throw DocumentImageError.unavailable
        }
        return data
    }
}

/// UI 测试用有状态合成模板桩；全部字段为合成值，不含真实模板或业务规则。
actor UITestTemplateAPI: TemplateAPI {
    private var templates = [UITestTemplateAPI.makeTemplate(id: "dddddddd-dddd-dddd-dddd-dddddddddddd", name: "合成默认模板", system: true)]

    func listTemplates() async throws -> [TemplateOut] { templates }

    func patchTemplate(id: UUID, _ payload: TemplatePatch) async throws -> TemplateOut {
        guard let index = templates.firstIndex(where: { UUID(uuidString: $0.id) == id }) else { throw AppError.validation }
        if payload.name == "FAIL" { throw AppError.unknown }
        let old = templates[index]
        let updated = TemplateOut(
            createdAt: old.createdAt, description: payload.description ?? old.description, id: old.id,
            isSystemDefault: old.isSystemDefault, name: payload.name ?? old.name,
            rules: payload.rules ?? old.rules, schemaVersion: old.schemaVersion, updatedAt: old.updatedAt
        )
        templates[index] = updated
        return updated
    }

    func cloneTemplate(id: UUID, _ payload: TemplateClone?) async throws -> TemplateOut {
        guard let old = templates.first(where: { UUID(uuidString: $0.id) == id }) else { throw AppError.validation }
        let copy = TemplateOut(
            createdAt: old.createdAt, description: old.description, id: UUID().uuidString,
            isSystemDefault: false, name: payload?.name ?? "合成默认模板 副本", rules: old.rules,
            schemaVersion: old.schemaVersion, updatedAt: old.updatedAt
        )
        templates.append(copy)
        return copy
    }

    private static func makeTemplate(id: String, name: String, system: Bool) -> TemplateOut {
        TemplateOut(
            createdAt: "2026-01-01T00:00:00Z", description: "\(name) 的合成说明", id: id,
            isSystemDefault: system, name: name, rules: syntheticRules,
            schemaVersion: "1.0", updatedAt: "2026-01-01T00:00:00Z"
        )
    }

    /// 单字面量合成规则（DEBUG UI 测试专用）；系统默认与副本共用，值均为占位。
    private static let syntheticRules: RuleTemplateDSL = {
        let json = #"{"categories":[],"compliance_rules":{"check_missing_airport_transfers":false,"deduplicate_reshot_receipts":true,"exclude_ride_hailing_tips":{"enabled":false,"highlight_flag":"tip_flag","providers":[]},"filter_irrelevant_files":true,"meal_split_for_multiple_diners":false,"require_invoice_payment_closure":true,"timezone_tolerance_days":2,"transit_direct_pay_exemptions":[]},"expense_types":[],"naming_and_export":{"airport_transfer_pattern":"{date}","excel_filename_pattern":"{batch}.xlsx","excel_group_levels":[],"standard_pattern":"{date}"},"region_hierarchy":{"in_scope":{"label":"境内","regions":[]},"out_of_scope":{"label":"境外","regions":[]},"preserve_original_currency":true},"schema_version":"1.0","template_code":"synthetic_template"}"#
        return try! JSONDecoder().decode(RuleTemplateDSL.self, from: Data(json.utf8))
    }()
}
#endif
