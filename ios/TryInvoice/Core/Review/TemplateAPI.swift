import Foundation

/// 模板页所需的最小服务端能力；只做读写转发，不承载任何合规或报销规则。
protocol TemplateAPI: Sendable {
    func listTemplates() async throws -> [TemplateOut]
    func patchTemplate(id: UUID, _ payload: TemplatePatch) async throws -> TemplateOut
    func cloneTemplate(id: UUID, _ payload: TemplateClone?) async throws -> TemplateOut
}

extension APIClient: TemplateAPI {}
