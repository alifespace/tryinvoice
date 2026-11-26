import XCTest

final class RootUITests: XCTestCase {
    func testLaunchShowsHomeTitleAndAppName() {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestAuthStub", "signedIn"]
        app.launch()

        XCTAssertTrue(app.navigationBars["首页"].exists)
        XCTAssertTrue(app.staticTexts["TryInvoice"].exists)
    }

    func testSignedInHomeExposesCaptureEntries() {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestAuthStub", "signedIn"]
        app.launch()

        let link = app.buttons["home.capture"]
        XCTAssertTrue(link.waitForExistence(timeout: 5))
        link.tap()
        for identifier in ["capture.scan", "capture.photos", "capture.files"] {
            XCTAssertTrue(app.descendants(matching: .any)[identifier].waitForExistence(timeout: 5))
        }
    }
}
