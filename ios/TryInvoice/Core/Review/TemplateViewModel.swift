import Combine
import Foundation

/// 模板页的状态与读写编排；合规开关只按原值转发服务端，不在客户端解释任何规则。
@MainActor
final class TemplateViewModel: ObservableObject {
    enum State: Equatable {
        case idle
        case loading
        case loaded
        case failed(AppError)
    }

    /// 页面上仅允许修改的 6 个合规开关。
    struct ComplianceToggles: Equatable, Sendable {
        let requireInvoicePaymentClosure: Bool
        let mealSplitForMultipleDiners: Bool
        let checkMissingAirportTransfers: Bool
        let deduplicateReshotReceipts: Bool
        let filterIrrelevantFiles: Bool
        let excludeRideHailingTips: Bool
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var templates: [TemplateOut] = []
    @Published private(set) var errorMessage: String?
    @Published private(set) var savingIds: Set<String> = []

    private let api: any TemplateAPI

    init(api: any TemplateAPI) {
        self.api = api
    }

    /// 失败时保留上一次成功列表，只切换状态与本页提示。
    func load() async {
        state = .loading
        do {
            templates = Self.ordered(try await api.listTemplates())
            state = .loaded
            errorMessage = nil
        } catch {
            fail(error)
        }
    }

    func isEditable(_ template: TemplateOut) -> Bool {
        !template.isSystemDefault
    }

    /// 复制已有模板；名称去空白，空名称交给服务端生成默认副本名。
    func clone(templateId: String, name: String?) async {
        guard let identifier = UUID(uuidString: templateId) else {
            errorMessage = AppError.validation.userMessage
            return
        }
        guard templates.contains(where: { $0.id == templateId }) else {
            return
        }

        let trimmedName = (name ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let payload = trimmedName.isEmpty
            ? TemplateClone(name: nil)
            : TemplateClone(name: trimmedName)

        do {
            let copy = try await api.cloneTemplate(id: identifier, payload)
            templates.append(copy)
            errorMessage = nil
        } catch {
            errorMessage = Self.appError(error).userMessage
        }
    }

    /// 保存自定义模板的说明、名称与 6 个开关；系统默认模板只读。
    func save(
        templateId: String,
        name: String,
        description: String,
        compliance: ComplianceToggles
    ) async {
        guard let identifier = UUID(uuidString: templateId) else {
            errorMessage = AppError.validation.userMessage
            return
        }
        guard let original = templates.first(where: { $0.id == templateId }) else {
            return
        }
        guard isEditable(original) else {
            errorMessage = AppError.validation.userMessage
            return
        }
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedName.isEmpty else {
            errorMessage = AppError.validation.userMessage
            return
        }
        guard !savingIds.contains(templateId) else {
            return
        }

        savingIds.insert(templateId)
        defer { savingIds.remove(templateId) }

        let payload = TemplatePatch(
            description: description,
            name: trimmedName,
            rules: Self.rules(original.rules, applying: compliance)
        )
        do {
            let updated = try await api.patchTemplate(id: identifier, payload)
            templates = templates.map { $0.id == templateId ? updated : $0 }
            errorMessage = nil
        } catch {
            errorMessage = Self.appError(error).userMessage
        }
    }

    static func toggles(of template: TemplateOut) -> ComplianceToggles {
        let rules = template.rules.complianceRules
        return ComplianceToggles(
            requireInvoicePaymentClosure: rules.requireInvoicePaymentClosure,
            mealSplitForMultipleDiners: rules.mealSplitForMultipleDiners,
            checkMissingAirportTransfers: rules.checkMissingAirportTransfers,
            deduplicateReshotReceipts: rules.deduplicateReshotReceipts,
            filterIrrelevantFiles: rules.filterIrrelevantFiles,
            excludeRideHailingTips: rules.excludeRideHailingTips.enabled
        )
    }

    /// 只替换 6 个授权开关；类别、地区、命名、时区容差等其余规则字段原样保留。
    static func rules(
        _ rules: RuleTemplateDSL,
        applying toggles: ComplianceToggles
    ) -> RuleTemplateDSL {
        let existing = rules.complianceRules
        return RuleTemplateDSL(
            categories: rules.categories,
            complianceRules: ComplianceRules(
                checkMissingAirportTransfers: toggles.checkMissingAirportTransfers,
                deduplicateReshotReceipts: toggles.deduplicateReshotReceipts,
                excludeRideHailingTips: TipExclusion(
                    enabled: toggles.excludeRideHailingTips,
                    highlightFlag: existing.excludeRideHailingTips.highlightFlag,
                    providers: existing.excludeRideHailingTips.providers
                ),
                filterIrrelevantFiles: toggles.filterIrrelevantFiles,
                mealSplitForMultipleDiners: toggles.mealSplitForMultipleDiners,
                requireInvoicePaymentClosure: toggles.requireInvoicePaymentClosure,
                timezoneToleranceDays: existing.timezoneToleranceDays,
                transitDirectPayExemptions: existing.transitDirectPayExemptions
            ),
            expenseTypes: rules.expenseTypes,
            namingAndExport: rules.namingAndExport,
            regionHierarchy: rules.regionHierarchy,
            schemaVersion: rules.schemaVersion,
            templateCode: rules.templateCode
        )
    }

    /// 稳定排序：系统默认模板整体前移，其余保持服务端顺序。
    private static func ordered(_ list: [TemplateOut]) -> [TemplateOut] {
        list.filter(\.isSystemDefault) + list.filter { !$0.isSystemDefault }
    }

    private func fail(_ error: Error) {
        let appError = Self.appError(error)
        state = .failed(appError)
        errorMessage = appError.userMessage
    }

    private static func appError(_ error: Error) -> AppError {
        (error as? AppError) ?? .unknown
    }
}
