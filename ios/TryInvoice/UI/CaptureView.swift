import AVFoundation
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers
import VisionKit

/// 采集入口：文档扫描、相册选择与文件导入；只把字节交给本地导入管线。
struct CaptureView: View {
    @StateObject private var viewModel: CaptureViewModel
    @State private var isScannerPresented = false
    @State private var isFileImporterPresented = false
    @State private var photoSelection: [PhotosPickerItem] = []

    init(pipeline: ImportPipeline, store: LocalStore) {
        _viewModel = StateObject(wrappedValue: CaptureViewModel(pipeline: pipeline, store: store))
    }

    var body: some View {
        VStack(spacing: 16) {
            Button(action: openScanner) { Label("扫描票据", systemImage: "doc.viewfinder") }
                .accessibilityIdentifier("capture.scan")
            PhotosPicker(selection: $photoSelection, maxSelectionCount: 20, matching: .images) {
                Label("从相册选择", systemImage: "photo.on.rectangle")
            }
            .accessibilityIdentifier("capture.photos")
            Button { isFileImporterPresented = true } label: { Label("导入文件", systemImage: "folder") }
                .accessibilityIdentifier("capture.files")
            if viewModel.state == .importing { ProgressView("正在导入…") }
            if let message = viewModel.feedback {
                Text(message).font(.callout).multilineTextAlignment(.center)
                    .accessibilityIdentifier("capture.feedback")
            }
        }
        .padding()
        .disabled(viewModel.state == .importing)
        .navigationTitle("导入票据")
        .onChange(of: photoSelection) { _, items in
            guard !items.isEmpty else { return }
            Task { @MainActor in
                await viewModel.importPhotos(loaders: items.map { item in
                    { try? await item.loadTransferable(type: Data.self) }
                })
                photoSelection = []
            }
        }
        .fileImporter(
            isPresented: $isFileImporterPresented,
            allowedContentTypes: [.pdf, .image],
            allowsMultipleSelection: true,
            onCompletion: { result in
                switch result {
                case let .success(urls): Task { await viewModel.importFileURLs(urls) }
                case .failure: Task { await viewModel.reportReadFailure() }
                }
            },
            onCancellation: { Task { await viewModel.reportCancelled() } }
        )
        .fullScreenCover(isPresented: $isScannerPresented) {
            DocumentScannerView(
                onFinish: { images in
                    isScannerPresented = false
                    Task { await viewModel.importScanPages(images) }
                },
                onCancel: {
                    isScannerPresented = false
                    Task { await viewModel.reportCancelled() }
                }
            ).ignoresSafeArea()
        }
    }

    /// 扫描前只读系统权限状态；仅在未决定时请求一次，被拒时给出确定性反馈。
    private func openScanner() {
        guard VNDocumentCameraViewController.isSupported else {
            viewModel.reportScanUnsupported()
            return
        }
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .denied, .restricted:
            viewModel.reportPermissionDenied()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { granted in
                Task { @MainActor in
                    if granted { isScannerPresented = true } else { viewModel.reportPermissionDenied() }
                }
            }
        default:
            isScannerPresented = true
        }
    }
}

/// VisionKit 文档相机的 SwiftUI 包装；取消与失败都按“未导入”处理。
private struct DocumentScannerView: UIViewControllerRepresentable {
    let onFinish: ([UIImage]) -> Void
    let onCancel: () -> Void

    func makeUIViewController(context: Context) -> VNDocumentCameraViewController {
        let controller = VNDocumentCameraViewController()
        controller.delegate = context.coordinator
        return controller
    }

    func updateUIViewController(_ controller: VNDocumentCameraViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onFinish: onFinish, onCancel: onCancel) }

    final class Coordinator: NSObject, VNDocumentCameraViewControllerDelegate {
        private let onFinish: ([UIImage]) -> Void
        private let onCancel: () -> Void
        init(onFinish: @escaping ([UIImage]) -> Void, onCancel: @escaping () -> Void) {
            self.onFinish = onFinish
            self.onCancel = onCancel
        }

        func documentCameraViewController(
            _ controller: VNDocumentCameraViewController, didFinishWith scan: VNDocumentCameraScan
        ) {
            let images = (0..<scan.pageCount).map { scan.imageOfPage(at: $0) }
            controller.dismiss(animated: true)
            onFinish(images)
        }

        func documentCameraViewControllerDidCancel(_ controller: VNDocumentCameraViewController) {
            controller.dismiss(animated: true)
            onCancel()
        }

        func documentCameraViewController(
            _ controller: VNDocumentCameraViewController, didFailWithError error: Error
        ) {
            controller.dismiss(animated: true)
            onCancel()
        }
    }
}
