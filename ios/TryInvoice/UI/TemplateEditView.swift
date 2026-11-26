import SwiftUI

/// 模板编辑页：只转发名称、说明与 6 个合规开关，其余规则字段由服务端原样保留。
struct TemplateEditView: View {
    let template: TemplateOut
    @ObservedObject var viewModel: TemplateViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var name: String
    @State private var summary: String
    @State private var requireClosure: Bool
    @State private var mealSplit: Bool
    @State private var airport: Bool
    @State private var dedupe: Bool
    @State private var filter: Bool
    @State private var tips: Bool

    init(template: TemplateOut, viewModel: TemplateViewModel) {
        self.template = template
        self.viewModel = viewModel
        let toggles = TemplateViewModel.toggles(of: template)
        _name = State(initialValue: template.name)
        _summary = State(initialValue: template.description)
        _requireClosure = State(initialValue: toggles.requireInvoicePaymentClosure)
        _mealSplit = State(initialValue: toggles.mealSplitForMultipleDiners)
        _airport = State(initialValue: toggles.checkMissingAirportTransfers)
        _dedupe = State(initialValue: toggles.deduplicateReshotReceipts)
        _filter = State(initialValue: toggles.filterIrrelevantFiles)
        _tips = State(initialValue: toggles.excludeRideHailingTips)
    }

    private var editable: Bool { viewModel.isEditable(template) }

    var body: some View {
        Form {
            Section {
                if editable {
                    TextField("名称", text: $name)
                        .accessibilityIdentifier("template.name")
                    TextField("说明", text: $summary)
                        .accessibilityIdentifier("template.desc")
                } else {
                    Text(template.name)
                    Text(template.description)
                }
                toggle("要求发票与付款闭环", $requireClosure, 0)
                toggle("多人用餐拆分", $mealSplit, 1)
                toggle("检查缺失机场交通", $airport, 2)
                toggle("去重重拍票据", $dedupe, 3)
                toggle("过滤无关文件", $filter, 4)
                toggle("剔除网约车小费", $tips, 5)
            }
            .disabled(!editable)

            Section {
                if editable {
                    Button("保存") { Task { await save() } }
                        .accessibilityIdentifier("template.save")
                } else {
                    Button("复制") { Task { await clone() } }
                        .accessibilityIdentifier("template.clone")
                }
            }
        }
        .navigationTitle(template.name)
        .errorBanner(message: viewModel.errorMessage, identifier: "template.error")
    }

    private func toggle(_ title: String, _ value: Binding<Bool>, _ index: Int) -> some View {
        Toggle(title, isOn: value)
            .accessibilityIdentifier("template.toggle.\(index)")
    }

    private func save() async {
        await viewModel.save(
            templateId: template.id,
            name: name,
            description: summary,
            compliance: TemplateViewModel.ComplianceToggles(
                requireInvoicePaymentClosure: requireClosure,
                mealSplitForMultipleDiners: mealSplit,
                checkMissingAirportTransfers: airport,
                deduplicateReshotReceipts: dedupe,
                filterIrrelevantFiles: filter,
                excludeRideHailingTips: tips
            )
        )
        if viewModel.errorMessage == nil {
            dismiss()
        }
    }

    private func clone() async {
        await viewModel.clone(templateId: template.id, name: nil)
        dismiss()
    }
}
