// External common-contract probe, copied only into frozen campaign test folders.
import 'dart:convert';
import 'dart:io';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:path/path.dart' as p;
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:sqflite_common/src/factory.dart' show SqfliteDatabaseFactory;
import 'package:catlogue/data/cat_database.dart';
import 'package:catlogue/models/auth_session.dart';
import 'package:catlogue/models/cat.dart';
import 'package:catlogue/models/cat_analysis.dart';
import 'package:catlogue/models/cat_reference_photo.dart';
import 'package:catlogue/stores/cat_store.dart';
import 'package:catlogue/stores/session_store.dart';

class Account extends SessionStore {
  @override
  bool get isSignedIn => false; // isolate local reads from remote services
  @override
  AuthSession get session => const AuthSession(userId: 'a', email: 'a@campaign.invalid', accessToken: '', refreshToken: '');
}

class QuietStore extends CatStore {
  QuietStore(SessionStore session) : super(session, networkAvailable: () async => false);
  @override
  Future<void> syncCatalog({bool manual = false}) async {}
  @override
  Future<void> syncPendingAnalyses({bool manual = false}) async {}
  @override
  Future<void> resolvePendingLocations() async {}
}

class Counted implements Database {
  Counted(this.delegate);
  final Database delegate;
  int reads = 0;
  @override
  Future<List<Map<String, Object?>>> query(String table, {bool? distinct, List<String>? columns, String? where, List<Object?>? whereArgs, String? groupBy, String? having, String? orderBy, int? limit, int? offset}) {
    reads++;
    return delegate.query(table, distinct: distinct, columns: columns, where: where, whereArgs: whereArgs, groupBy: groupBy, having: having, orderBy: orderBy, limit: limit, offset: offset);
  }
  @override
  Batch batch() => delegate.batch();
  @override
  Future<void> close() => delegate.close();
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class Factory implements SqfliteDatabaseFactory {
  Factory(this.delegate);
  final DatabaseFactory delegate;
  late Counted counted;
  @override
  Future<Database> openDatabase(String path, {OpenDatabaseOptions? options}) async => counted = Counted(await delegate.openDatabase(path, options: options));
  @override
  Future<String> getDatabasesPath() => delegate.getDatabasesPath();
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test('DB-01 common public CatStore load contract (no remote IO)', () async {
    final output = Platform.environment['QUALITY_FLUTTER_OUTPUT'];
    expect(output, isNotNull);
    sqfliteFfiInit();
    final folder = await Directory.systemTemp.createTemp('quality-pilot-sqlite-');
    final originalFactory = databaseFactoryFfi;
    await originalFactory.setDatabasesPath(folder.path);
    final factory = Factory(originalFactory);
    databaseFactory = factory;
    final account = Account();
    final store = QuietStore(account);
    final messenger = TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    const channel = MethodChannel('dev.fluttercommunity.plus/connectivity_status');
    messenger.setMockMethodCallHandler(channel, (_) async => null);
    final db = await CatDatabase.instance.database;
    try {
      final batch = db.batch();
      for (final id in [...List.generate(40, (i) => 'a-$i'), 'guest', 'foreign', 'deleted']) {
        final cat = Cat(id: id, captureNumber: 1, photoPath: '$id.png', capturedAt: DateTime.utc(2026, 10, 21),
          ownerId: id == 'guest' ? null : id == 'foreign' ? 'b' : 'a', deletedAt: id == 'deleted' ? DateTime.utc(2026, 10, 21) : null);
        batch.insert('cats', cat.toMap());
        batch.insert('cat_analyses', CatAnalysis(catId: id, status: AnalysisStatus.completed, coatType: 'Fixture').toMap());
        for (final kind in ['front', 'left', 'back']) {
          batch.insert('cat_reference_photos', CatReferencePhoto(catId: id, kind: kind, localPath: '$id/$kind.png', status: 'ready', updatedAt: DateTime.utc(2026, 10, 21), deletedAt: kind == 'back' ? DateTime.utc(2026, 10, 21) : null).toMap());
        }
      }
      await batch.commit(noResult: true);
      final samples = <Map<String, Object?>>[];
      for (var i = 0; i < 23; i++) {
        factory.counted.reads = 0;
        final watch = Stopwatch()..start();
        await store.loadCats();
        watch.stop();
        final ids = store.cats.map((c) => c.id).toSet();
        expect(ids, {...List.generate(40, (i) => 'a-$i'), 'guest'});
        for (final id in ids) {
          expect(store.analysisFor(id)?.coatType, 'Fixture');
          expect(store.referencePhotosFor(id).map((p) => p.kind).toList(), ['front', 'left']);
        }
        samples.add({'elapsed_us': watch.elapsedMicroseconds, 'queries': factory.counted.reads, 'warmup': i < 3, 'visible': ids.length});
      }
      await File(output!).writeAsString(jsonEncode({'scenario': 'DB-01', 'status': 'PASS', 'samples': samples,
        'limitations': 'real SQLite FFI desktop, public loadCats; background network paths overridden; no phone latency',
        'expected_visible': 41, 'resources': {'rss_bytes': ProcessInfo.currentRss, 'peak_rss_bytes': ProcessInfo.maxRss}}));
    } finally {
      store.dispose();
      account.dispose();
      await db.close();
      messenger.setMockMethodCallHandler(channel, null);
      expect(p.isWithin(Directory.systemTemp.absolute.path, folder.absolute.path), isTrue);
      await folder.delete(recursive: true);
    }
  });
}
