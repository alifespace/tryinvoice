import XCTest

/// 复核关键路径 UI 测试：全部使用 DEBUG 合成桩，不触网、不含真实票据数据。
final class ReviewUITests: XCTestCase {
    private let flaggedId = "11111111-1111-1111-1111-111111111111"
    private let batchId = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"

    override func setUp() {
        continueAfterFailure = false
    }

    private func launchApp() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestAuthStub", "signedIn"]
        app.launch()
        return app
    }

    private func any(_ app: XCUIApplication, _ identifier: String) -> XCUIElement {
        app.descendants(matching: .any)[identifier]
    }

    private func openBatch(_ app: XCUIApplication) {
        let review = app.buttons["home.review"]
        XCTAssertTrue(review.waitForExistence(timeout: 10))
        review.tap()

        let batch = any(app, "review.batch.\(batchId)")
        XCTAssertTrue(batch.waitForExistence(timeout: 10))
        batch.tap()
    }

    private func openFlaggedTransaction(_ app: XCUIApplication) -> XCUIElement {
        openBatch(app)
        let row = app.staticTexts["合成小馆"]
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        row.tap()
        return row
    }

    /// 先清空输入框再输入，避免与预填内容拼接。
    private func replaceText(_ element: XCUIElement, with text: String) {
        element.tap()
        let current = (element.value as? String) ?? ""
        if !current.isEmpty {
            element.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: current.count))
        }
        element.typeText(text)
    }

    func testBatchShowsAlertsAndFlaggedTransaction() {
        let app = launchApp()
        openBatch(app)

        XCTAssertTrue(any(app, "review.alerts").waitForExistence(timeout: 10))
        XCTAssertTrue(any(app, "review.flag.\(flaggedId)").waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["合成小馆"].exists)
    }

    func testDetailSwitchesToReceiptImage() {
        let app = launchApp()
        _ = openFlaggedTransaction(app)

        let picker = app.segmentedControls["detail.docPicker"]
        XCTAssertTrue(picker.waitForExistence(timeout: 10))
        picker.buttons["Receipt"].tap()

        let unavailable = any(app, "detail.imageUnavailable")
        XCTAssertTrue(
            unavailable.waitForExistence(timeout: 5) || app.images.firstMatch.waitForExistence(timeout: 5)
        )
    }

    func testEditedNotesPersistAfterReenteringDetail() {
        let app = launchApp()
        _ = openFlaggedTransaction(app)

        let notes = app.textFields["detail.notes"]
        XCTAssertTrue(notes.waitForExistence(timeout: 10))
        notes.tap()
        notes.typeText("OK\n")
        app.buttons["detail.save"].tap()

        XCTAssertTrue(app.navigationBars["复核"].waitForExistence(timeout: 10))
        let batchRow = app.staticTexts["合成小馆"]
        XCTAssertTrue(batchRow.waitForExistence(timeout: 10))
        batchRow.tap()

        let reloaded = app.textFields["detail.notes"]
        XCTAssertTrue(reloaded.waitForExistence(timeout: 10))
        let value = (reloaded.value as? String) ?? ""
        XCTAssertTrue(value.contains("OK"), value)
    }

    func testLinkCandidateSelectionSavesAndReturns() {
        let app = launchApp()
        _ = openFlaggedTransaction(app)

        let menu = any(app, "detail.link.invoice")
        XCTAssertTrue(menu.waitForExistence(timeout: 10))
        menu.tap()
        let option = app.buttons["synthetic-extra.pdf"]
        XCTAssertTrue(option.waitForExistence(timeout: 5))
        option.tap()

        app.buttons["detail.save"].tap()
        XCTAssertTrue(app.navigationBars["复核"].waitForExistence(timeout: 10))
        XCTAssertFalse(any(app, "detail.error").exists)

        // 已占用候选不得残留：回到列表后再开另一笔交易，其菜单不应再出现同一凭证。
        let other = app.staticTexts["合成出行"]
        XCTAssertTrue(other.waitForExistence(timeout: 10))
        other.tap()
        let otherMenu = any(app, "detail.link.invoice")
        XCTAssertTrue(otherMenu.waitForExistence(timeout: 10))
        otherMenu.tap()
        XCTAssertFalse(app.buttons["synthetic-extra.pdf"].exists)
    }

    func testSaveFailureShowsErrorAndStaysOnDetail() {
        let app = launchApp()
        _ = openFlaggedTransaction(app)

        let notes = app.textFields["detail.notes"]
        XCTAssertTrue(notes.waitForExistence(timeout: 10))
        replaceText(notes, with: "FAIL\n")
        app.buttons["detail.save"].tap()

        XCTAssertTrue(any(app, "detail.error").waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["detail.save"].exists)
    }
}
