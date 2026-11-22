import XCTest

final class RootUITests: XCTestCase {
    func testLaunchShowsHomeTitleAndAppName() {
        let app = XCUIApplication()
        app.launchArguments = ["-UITestAuthStub", "signedIn"]
        app.launch()

        XCTAssertTrue(app.navigationBars["首页"].exists)
        XCTAssertTrue(app.staticTexts["TryInvoice"].exists)
    }
}
