import SwiftUI

/// 模板列表页：只展示服务端模板名称与系统默认标记，不解释任何合规规则。
struct TemplateListView: View {
    @StateObject private var viewModel: TemplateViewModel

    init(api: any TemplateAPI) {
        _viewModel = StateObject(wrappedValue: TemplateViewModel(api: api))
    }

    var body: some View {
        List {
            ForEach(viewModel.templates, id: \.id) { template in
                NavigationLink {
                    TemplateEditView(template: template, viewModel: viewModel)
                } label: {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(template.name)
                        if template.isSystemDefault {
                            Text("系统默认")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .accessibilityIdentifier("template.badge.system")
                        }
                    }
                }
                .accessibilityIdentifier("template.row.\(template.id)")
            }
        }
        .navigationTitle("模板")
        .errorBanner(message: viewModel.errorMessage, identifier: "template.error")
        .task { await viewModel.load() }
    }
}
