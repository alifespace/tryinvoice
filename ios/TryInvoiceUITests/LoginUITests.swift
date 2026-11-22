import XCTest

final class LoginUITests: XCTestCase {
    func testLoginOpensHome() {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestAuthStub", "signedOut"]
        app.launch()

        let email = app.textFields["login.email"]
        let password = app.secureTextFields["login.password"]
        XCTAssertTrue(email.waitForExistence(timeout: 5))
        email.tap()
        email.typeText("user@example.test")
        password.tap()
        password.typeText("synthetic-password")
        app.buttons["login.submit"].tap()

        XCTAssertTrue(app.staticTexts["TryInvoice"].waitForExistence(timeout: 5))
    }

    func testCancelingDeleteConfirmationDoesNotSignOut() {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestAuthStub", "signedIn"]
        app.launch()

        let accountLink = app.buttons["home.account"]
        XCTAssertTrue(accountLink.waitForExistence(timeout: 5))
        accountLink.tap()
        app.buttons["account.delete"].tap()
        app.buttons["取消"].tap()

        XCTAssertTrue(app.staticTexts["test@example.test"].exists)
        XCTAssertTrue(app.buttons["account.delete"].exists)
    }
}
