import XCTest

/// 模板页关键路径 UI 测试：全部使用 DEBUG 合成桩，不触网、不含真实模板数据。
final class TemplateUITests: XCTestCase {
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

    private func openTemplates(_ app: XCUIApplication) {
        let entry = app.buttons["home.templates"]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        entry.tap()
        XCTAssertTrue(app.staticTexts["合成默认模板"].waitForExistence(timeout: 10))
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

    func testSystemDefaultIsReadOnlyWithCloneAction() {
        let app = launchApp()
        openTemplates(app)
        XCTAssertTrue(any(app, "template.badge.system").waitForExistence(timeout: 10))

        app.staticTexts["合成默认模板"].tap()
        XCTAssertTrue(app.buttons["template.clone"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["template.save"].exists)
    }

    func testCloneRenameSaveAndReload() {
        let app = launchApp()
        openTemplates(app)
        app.staticTexts["合成默认模板"].tap()
        app.buttons["template.clone"].tap()

        let copy = app.staticTexts["合成默认模板 副本"]
        XCTAssertTrue(copy.waitForExistence(timeout: 10))
        copy.tap()

        let name = app.textFields["template.name"]
        XCTAssertTrue(name.waitForExistence(timeout: 10))
        replaceText(name, with: "改名副本")
        app.buttons["template.save"].tap()
        XCTAssertTrue(app.staticTexts["改名副本"].waitForExistence(timeout: 10))

        // 返回首页再进入，模板页重新 load，名称应保持。
        let back = app.navigationBars["模板"].buttons.element(boundBy: 0)
        XCTAssertTrue(back.waitForExistence(timeout: 10))
        back.tap()
        openTemplates(app)
        let renamed = app.staticTexts["改名副本"]
        XCTAssertTrue(renamed.waitForExistence(timeout: 10))
        renamed.tap()

        let again = app.textFields["template.name"]
        XCTAssertTrue(again.waitForExistence(timeout: 10))
        replaceText(again, with: "FAIL")
        app.buttons["template.save"].tap()
        XCTAssertTrue(any(app, "template.error").waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["template.save"].exists)
    }
}
