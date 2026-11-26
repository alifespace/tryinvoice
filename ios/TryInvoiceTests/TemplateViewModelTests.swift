import Foundation
import XCTest
@testable import TryInvoice

final class TemplateViewModelTests: XCTestCase {
    private let systemId = "bbbbbbbb-0000-4000-8000-000000000001"
    private let customId = "bbbbbbbb-0000-4000-8000-000000000002"
    private let secondId = "bbbbbbbb-0000-4000-8000-000000000003"
    private let copyId = "bbbbbbbb-0000-4000-8000-000000000004"

    @MainActor
    func testLoadPutsSystemDefaultFirstAndKeepsServerOrder() async {
        let api = StubTemplateAPI()
        await api.enqueueList(.success([
            template(customId, "自定义 A"),
            template(systemId, "系统默认", system: true),
            template(secondId, "自定义 B"),
        ]))
        let viewModel = TemplateViewModel(api: api)
        await viewModel.load()

        XCTAssertEqual(viewModel.state, .loaded)
        XCTAssertNil(viewModel.errorMessage)
        XCTAssertEqual(viewModel.templates.map(\.id), [systemId, customId, secondId])
        XCTAssertFalse(viewModel.isEditable(viewModel.templates[0]))
        XCTAssertTrue(viewModel.isEditable(viewModel.templates[1]))
        let calls = await api.listCalls()
        XCTAssertEqual(calls, 1)
    }

    @MainActor
    func testLoadFailureKeepsPreviousTemplatesAndSetsFailedState() async {
        let api = StubTemplateAPI()
        await api.enqueueList(.success([template(customId, "自定义 A")]))
        let viewModel = TemplateViewModel(api: api)
        await viewModel.load()
        let loaded = viewModel.templates

        await api.enqueueList(.failure(.network))
        await viewModel.load()

        XCTAssertEqual(viewModel.state, .failed(.network))
        XCTAssertEqual(viewModel.templates, loaded)
        XCTAssertEqual(viewModel.errorMessage, AppError.network.userMessage)
    }

    @MainActor
    func testCloneAppendsServerCopyAndTrimsName() async {
        let api = StubTemplateAPI()
        await api.enqueueList(.success([template(systemId, "系统默认", system: true)]))
        await api.enqueueClone(.success(template(copyId, "我的副本")))
        let viewModel = TemplateViewModel(api: api)
        await viewModel.load()

        await viewModel.clone(templateId: systemId, name: "  我的副本  ")

        XCTAssertEqual(viewModel.templates.map(\.id), [systemId, copyId])
        XCTAssertEqual(viewModel.templates.last?.name, "我的副本")
        XCTAssertNil(viewModel.errorMessage)
        let calls = await api.cloneCalls()
        XCTAssertEqual(calls.count, 1)
        XCTAssertEqual(calls.first?.id, UUID(uuidString: systemId))
        XCTAssertEqual(calls.first?.payload, TemplateClone(name: "我的副本"))
    }

    @MainActor
    func testCloneWithBlankNameSendsNilName() async {
        let api = StubTemplateAPI()
        await api.enqueueList(.success([template(systemId, "系统默认", system: true)]))
        await api.enqueueClone(.success(template(copyId, "系统默认 副本")))
        let viewModel = TemplateViewModel(api: api)
        await viewModel.load()

        await viewModel.clone(templateId: systemId, name: "   ")

        let calls = await api.cloneCalls()
        XCTAssertEqual(calls.count, 1)
        XCTAssertEqual(calls.first?.payload, TemplateClone(name: nil))
        XCTAssertEqual(viewModel.templates.last?.id, copyId)
    }

    @MainActor
    func testCloneFailureKeepsListAndSetsMessage() async {
        let api = StubTemplateAPI()
        await api.enqueueList(.success([template(systemId, "系统默认", system: true)]))
        await api.enqueueClone(.failure(.server))
        let viewModel = TemplateViewModel(api: api)
        await viewModel.load()
        let loaded = viewModel.templates

        await viewModel.clone(templateId: systemId, name: nil)

        XCTAssertEqual(viewModel.templates, loaded)
        XCTAssertEqual(viewModel.errorMessage, AppError.server.userMessage)
        XCTAssertEqual(viewModel.state, .loaded)
    }

    /// 系统默认模板只读：即使 UI 误调用也必须零请求。
    @MainActor
    func testSaveRejectsSystemDefaultWithoutRequest() async {
        let api = StubTemplateAPI()
        let system = template(systemId, "系统默认", system: true)
        await api.enqueueList(.success([system]))
        let viewModel = TemplateViewModel(api: api)
        await viewModel.load()

        await viewModel.save(
            templateId: systemId, name: "改个名字", description: "不应生效",
            compliance: TemplateViewModel.toggles(of: system)
        )

        XCTAssertEqual(viewModel.errorMessage, AppError.validation.userMessage)
        XCTAssertEqual(viewModel.templates, [system])
        let calls = await api.patchCalls()
        XCTAssertTrue(calls.isEmpty)
    }

    @MainActor
    func testSaveRejectsBlankNameWithoutRequest() async {
        let api = StubTemplateAPI()
        let custom = template(customId, "自定义 A")
        await api.enqueueList(.success([custom]))
        let viewModel = TemplateViewModel(api: api)
        await viewModel.load()

        await viewModel.save(
            templateId: customId, name: "   ", description: "说明",
            compliance: TemplateViewModel.toggles(of: custom)
        )

        XCTAssertEqual(viewModel.errorMessage, AppError.validation.userMessage)
        XCTAssertEqual(viewModel.templates, [custom])
        let calls = await api.patchCalls()
        XCTAssertTrue(calls.isEmpty)
    }

    /// 请求体只能改 6 个开关；其余规则字段必须与服务端原值完全一致。
    @MainActor
    func testSaveSendsOnlyToggleChangesAndReconcilesWithServer() async {
        let api = StubTemplateAPI()
        let original = template(customId, "自定义 A")
        let toggles = TemplateViewModel.ComplianceToggles(
            requireInvoicePaymentClosure: false, mealSplitForMultipleDiners: true,
            checkMissingAirportTransfers: true, deduplicateReshotReceipts: false,
            filterIrrelevantFiles: false, excludeRideHailingTips: true
        )
        let serverResult = TemplateOut(
            createdAt: "2026-01-01T00:00:00Z", description: "服务端说明", id: customId,
            isSystemDefault: false, name: "服务端名称",
            rules: TemplateViewModel.rules(original.rules, applying: toggles),
            schemaVersion: "1.0", updatedAt: "2026-02-02T00:00:00Z"
        )
        await api.enqueueList(.success([original]))
        await api.enqueuePatch(.success(serverResult), for: customId)
        let viewModel = TemplateViewModel(api: api)
        await viewModel.load()

        await viewModel.save(
            templateId: customId, name: "  新名称  ", description: "新说明", compliance: toggles
        )

        let calls = await api.patchCalls()
        XCTAssertEqual(calls.count, 1)
        XCTAssertEqual(calls.first?.id, UUID(uuidString: customId))
        let payload = calls.first?.payload
        XCTAssertEqual(payload?.name, "新名称")
        XCTAssertEqual(payload?.description, "新说明")
        let rules = payload?.rules
        XCTAssertEqual(rules?.complianceRules.requireInvoicePaymentClosure, false)
        XCTAssertEqual(rules?.complianceRules.mealSplitForMultipleDiners, true)
        XCTAssertEqual(rules?.complianceRules.checkMissingAirportTransfers, true)
        XCTAssertEqual(rules?.complianceRules.deduplicateReshotReceipts, false)
        XCTAssertEqual(rules?.complianceRules.filterIrrelevantFiles, false)
        XCTAssertEqual(rules?.complianceRules.excludeRideHailingTips.enabled, true)
        XCTAssertEqual(rules?.complianceRules.excludeRideHailingTips.highlightFlag, "tip_flag")
        XCTAssertEqual(rules?.complianceRules.excludeRideHailingTips.providers, ["didi", "uber"])
        XCTAssertEqual(rules?.complianceRules.timezoneToleranceDays, 2)
        XCTAssertEqual(rules?.complianceRules.transitDirectPayExemptions, ["transit_card"])
        XCTAssertEqual(rules?.categories, original.rules.categories)
        XCTAssertEqual(rules?.expenseTypes, original.rules.expenseTypes)
        XCTAssertEqual(rules?.namingAndExport, original.rules.namingAndExport)
        XCTAssertEqual(rules?.regionHierarchy, original.rules.regionHierarchy)
        XCTAssertEqual(rules?.schemaVersion, original.rules.schemaVersion)
        XCTAssertEqual(rules?.templateCode, original.rules.templateCode)

        XCTAssertEqual(viewModel.templates, [serverResult])
        XCTAssertEqual(TemplateViewModel.toggles(of: serverResult), toggles)

        await api.enqueueList(.success([serverResult]))
        await viewModel.load()
        XCTAssertEqual(viewModel.templates, [serverResult])
    }

    @MainActor
    func testSaveFailureKeepsLocalTemplate() async {
        let api = StubTemplateAPI()
        let original = template(customId, "自定义 A")
        await api.enqueueList(.success([original]))
        await api.enqueuePatch(.failure(.server), for: customId)
        let viewModel = TemplateViewModel(api: api)
        await viewModel.load()

        await viewModel.save(
            templateId: customId, name: "未生效", description: "未生效",
            compliance: TemplateViewModel.toggles(of: original)
        )

        XCTAssertEqual(viewModel.templates, [original])
        XCTAssertEqual(viewModel.errorMessage, AppError.server.userMessage)
        XCTAssertTrue(viewModel.savingIds.isEmpty)
    }

    /// 同 id 在途时第二次保存直接返回，不产生第二个请求。
    @MainActor
    func testConcurrentSaveForSameTemplateIsRejected() async {
        let api = StubTemplateAPI()
        let gate = TemplateGate()
        let original = template(customId, "自定义 A")
        let toggles = TemplateViewModel.toggles(of: original)
        await api.enqueueList(.success([original]))
        await api.enqueuePatch(.success(original), for: customId)
        await api.setGate(gate, for: customId)
        let viewModel = TemplateViewModel(api: api)
        await viewModel.load()

        let firstSave = Task {
            await viewModel.save(
                templateId: customId, name: "第一次", description: "说明", compliance: toggles
            )
        }
        await api.waitForPatchCount(1)
        XCTAssertEqual(viewModel.savingIds, [customId])

        await viewModel.save(
            templateId: customId, name: "第二次", description: "说明", compliance: toggles
        )

        let inFlightCalls = await api.patchCalls()
        XCTAssertEqual(inFlightCalls.count, 1)
        XCTAssertTrue(viewModel.savingIds.contains(customId))

        await gate.release()
        await firstSave.value
        XCTAssertTrue(viewModel.savingIds.isEmpty)
        let finalCalls = await api.patchCalls()
        XCTAssertEqual(finalCalls.count, 1)
    }

    @MainActor
    func testInvalidTemplateIdentifierSendsNoRequest() async {
        let api = StubTemplateAPI()
        let viewModel = TemplateViewModel(api: api)
        let toggles = TemplateViewModel.ComplianceToggles(
            requireInvoicePaymentClosure: true, mealSplitForMultipleDiners: false,
            checkMissingAirportTransfers: false, deduplicateReshotReceipts: true,
            filterIrrelevantFiles: true, excludeRideHailingTips: false
        )

        await viewModel.save(
            templateId: "not-a-uuid", name: "名称", description: "说明", compliance: toggles
        )
        XCTAssertEqual(viewModel.errorMessage, AppError.validation.userMessage)

        await viewModel.clone(templateId: "not-a-uuid", name: "副本")

        let patches = await api.patchCalls()
        let clones = await api.cloneCalls()
        XCTAssertTrue(patches.isEmpty)
        XCTAssertTrue(clones.isEmpty)
    }

    private func template(
        _ id: String, _ name: String, system: Bool = false, requireClosure: Bool = true,
        mealSplit: Bool = false, airport: Bool = false, dedupeReshot: Bool = true,
        filterIrrelevant: Bool = true, excludeTips: Bool = false
    ) -> TemplateOut {
        TemplateOut(
            createdAt: "2026-01-01T00:00:00Z", description: "\(name) 的说明", id: id,
            isSystemDefault: system, name: name,
            rules: rules(
                requireClosure: requireClosure, mealSplit: mealSplit, airport: airport,
                dedupeReshot: dedupeReshot, filterIrrelevant: filterIrrelevant,
                excludeTips: excludeTips
            ),
            schemaVersion: "1.0", updatedAt: "2026-01-01T00:00:00Z"
        )
    }

    private func rules(
        requireClosure: Bool, mealSplit: Bool, airport: Bool, dedupeReshot: Bool,
        filterIrrelevant: Bool, excludeTips: Bool
    ) -> RuleTemplateDSL {
        RuleTemplateDSL(
            categories: [Category(code: "meals", group: "living", label: "餐饮")],
            complianceRules: ComplianceRules(
                checkMissingAirportTransfers: airport, deduplicateReshotReceipts: dedupeReshot,
                excludeRideHailingTips: TipExclusion(
                    enabled: excludeTips, highlightFlag: "tip_flag", providers: ["didi", "uber"]
                ),
                filterIrrelevantFiles: filterIrrelevant, mealSplitForMultipleDiners: mealSplit,
                requireInvoicePaymentClosure: requireClosure, timezoneToleranceDays: 2,
                transitDirectPayExemptions: ["transit_card"]
            ),
            expenseTypes: [ExpenseType(code: "travel", label: "差旅", requireTripReference: true)],
            namingAndExport: NamingAndExport(
                airportTransferPattern: "{date}_{merchant}", excelFilenamePattern: "{batch}.xlsx",
                excelGroupLevels: ["category", "region"], standardPattern: "{date}_{category}_{amount}"
            ),
            regionHierarchy: RegionHierarchy(
                inScope: RegionScope(
                    label: "境内",
                    regions: [Region(code: "CN-BJ", currencies: ["CNY", "USD"], label: "北京")]
                ),
                outOfScope: RegionScope(
                    label: "境外",
                    regions: [Region(code: "US-CA", currencies: ["USD"], label: "加州")]
                ),
                preserveOriginalCurrency: true
            ),
            schemaVersion: "1.0", templateCode: "synthetic_template"
        )
    }
}

/// 记录模板调用并按 id 分队列返回结果；每个 id 的闸门只拦第一次请求。
actor StubTemplateAPI: TemplateAPI {
    private var lists: [Result<[TemplateOut], AppError>] = []
    private var patches: [String: [Result<TemplateOut, AppError>]] = [:]
    private var clones: [Result<TemplateOut, AppError>] = []
    private var gates: [String: TemplateGate] = [:]
    private var patchLog: [(id: UUID, payload: TemplatePatch)] = []
    private var cloneLog: [(id: UUID, payload: TemplateClone?)] = []
    private var listLog = 0
    private var waiters: [(count: Int, continuation: CheckedContinuation<Void, Never>)] = []

    func enqueueList(_ result: Result<[TemplateOut], AppError>) { lists.append(result) }

    func enqueuePatch(_ result: Result<TemplateOut, AppError>, for id: String) {
        patches[key(id), default: []].append(result)
    }

    func enqueueClone(_ result: Result<TemplateOut, AppError>) { clones.append(result) }
    func setGate(_ gate: TemplateGate, for id: String) { gates[key(id)] = gate }

    func listCalls() -> Int { listLog }
    func patchCalls() -> [(id: UUID, payload: TemplatePatch)] { patchLog }
    func cloneCalls() -> [(id: UUID, payload: TemplateClone?)] { cloneLog }

    func waitForPatchCount(_ count: Int) async {
        guard patchLog.count < count else { return }
        await withCheckedContinuation { waiters.append((count, $0)) }
    }

    func listTemplates() async throws -> [TemplateOut] {
        listLog += 1
        guard let next = lists.first else { throw AppError.unknown }
        lists.removeFirst()
        return try next.get()
    }

    func patchTemplate(id: UUID, _ payload: TemplatePatch) async throws -> TemplateOut {
        patchLog.append((id, payload))
        let pending = waiters
        waiters = pending.filter { $0.count > patchLog.count }
        pending.filter { $0.count <= patchLog.count }.forEach { $0.continuation.resume() }

        let identifier = key(id.uuidString)
        if let gate = gates.removeValue(forKey: identifier) { await gate.wait() }
        guard var queue = patches[identifier], !queue.isEmpty else { throw AppError.unknown }
        let next = queue.removeFirst()
        patches[identifier] = queue
        return try next.get()
    }

    func cloneTemplate(id: UUID, _ payload: TemplateClone?) async throws -> TemplateOut {
        cloneLog.append((id, payload))
        guard !clones.isEmpty else { throw AppError.unknown }
        return try clones.removeFirst().get()
    }

    private func key(_ id: String) -> String { id.lowercased() }
}

actor TemplateGate {
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private var open = false

    func wait() async {
        guard !open else { return }
        await withCheckedContinuation { waiters.append($0) }
    }

    func release() {
        open = true
        waiters.forEach { $0.resume() }
        waiters = []
    }
}
