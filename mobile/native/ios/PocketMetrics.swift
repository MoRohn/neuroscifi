import SwiftUI
import AppIntents
import UniformTypeIdentifiers

// Add this file as the single app entry point in an iOS/iPadOS 17+ SwiftUI target.
actor Marks {
    static let shared = Marks()
    private var file: URL {
        FileManager.default.urls(for:.applicationSupportDirectory, in:.userDomainMask)[0]
            .appendingPathComponent("PocketMarks", isDirectory:true).appendingPathComponent("minutes.json")
    }
    func setEnabled(_ value: Bool) {
        UserDefaults.standard.set(value, forKey: "local.hour.enabled")
        if !value { try? FileManager.default.removeItem(at:file) }
    }
    func enabled() -> Bool { UserDefaults.standard.bool(forKey: "local.hour.enabled") }
    private func store(_ minutes: [Int]) throws {
        var dir = file.deletingLastPathComponent()
        try FileManager.default.createDirectory(at:dir, withIntermediateDirectories:true)
        var policy=URLResourceValues(); policy.isExcludedFromBackup=true
        try dir.setResourceValues(policy)
        try JSONEncoder().encode(minutes).write(to:file, options:[.atomic,.completeFileProtection])
    }
    func recent(_ now: Double) -> [Int] {
        guard let data=try? Data(contentsOf:file), let old=try? JSONDecoder().decode([Int].self,from:data) else {return []}
        let minute=Int(now/60), live=old.filter { $0 > minute-60 && $0 <= minute }
        if old != live { try? store(live) }
        return live
    }
    func mark() {
        guard enabled() else { return }
        let now=Date().timeIntervalSince1970
        var values=recent(now); values.append(Int(now/60))
        try? store(Array(values.suffix(64)))
    }
}
struct ObserveSelectedAppOpen: AppIntent {
    static var title: LocalizedStringResource = "Mark a selected app opening"
    func perform() async throws -> some IntentResult {
        await Marks.shared.mark()
        return .result()
    }
}
struct Receipt: FileDocument {
    static var readableContentTypes: [UTType] { [.json] }
    var bytes: Data
    init(_ bytes: Data = Data()) { self.bytes = bytes }
    init(configuration: ReadConfiguration) throws { bytes = configuration.file.regularFileContents ?? Data() }
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper { FileWrapper(regularFileWithContents: bytes) }
}
struct MetricsView: View {
    @State private var enabled = false
    @State private var system = false
    @State private var includeMarks = false
    @State private var receipt = Receipt()
    @State private var display = ""
    @State private var saving = false
    var body: some View {
        Form {
            Section("Selected app openings") {
                Toggle("Allow marks from my Shortcuts automations", isOn: Binding(get:{enabled},set:{ value in
                    enabled=value; display=""; receipt=Receipt()
                    Task { await Marks.shared.setEnabled(value) }
                }))
                Text("In Shortcuts, choose the apps and run Mark a selected app opening when one opens. Turning this off clears marks.")
                Toggle("Include opening band for the last hour",isOn:$includeMarks)
            }
            Section("Device state") { Toggle("Include current power and thermal state",isOn:$system) }
            Button("Review metrics") { Task { await review() } }
            if !display.isEmpty {
                Text(display).font(.caption.monospaced())
                Button("Export reviewed receipt") { saving=true }
            }
        }.task { enabled=await Marks.shared.enabled() }
        .onChange(of: system){ _,_ in display=""; receipt=Receipt() }
        .onChange(of: includeMarks){ _,_ in display=""; receipt=Receipt() }
        .fileExporter(isPresented:$saving,document:receipt,contentType:.json,defaultFilename:"pocket-device"){ _ in display="";receipt=Receipt() }
    }
    @MainActor func review() async {
        var f=[String:[String]]()
        if system {
            let p=ProcessInfo.processInfo
            f["power"]=[p.isLowPowerModeEnabled ? "saving":"standard","ios"]
            switch p.thermalState {
            case .nominal: f["thermal"]=["normal","ios"]
            case .fair: f["thermal"]=["elevated","ios"]
            case .serious,.critical: f["thermal"]=["high","ios"]
            @unknown default: break
            }
        }
        if enabled && includeMarks {
            let n=await Marks.shared.recent(Date().timeIntervalSince1970).count
            if n>0 { f["switches"]=[n<=2 ? "few":n<=8 ? "some":"many","shortcut"] }
        }
        guard !f.isEmpty, let bytes=try? JSONSerialization.data(withJSONObject:["schema":"pocket-device-1","features":f],options:[.sortedKeys]) else { display="";receipt=Receipt(); return }
        receipt=Receipt(bytes); display=String(decoding:bytes,as:UTF8.self)
    }
}
@main struct PocketMetricsApp: App { var body: some Scene { WindowGroup { MetricsView() } } }
