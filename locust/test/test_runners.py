import itertools
import unittest

import gevent
from gevent import sleep
from gevent.queue import Queue
import six

import mock
from locust import events
from locust.core import Locust, TaskSet, task
from locust.exception import LocustError
from locust.main import parse_options
from locust.rpc import Message
from locust.runners import LocalLocustRunner, MasterLocustRunner
from locust.stats import global_stats, RequestStats
from locust.test.testcases import LocustTestCase


def mocked_rpc_server():
    class MockedRpcServer(object):
        queue = Queue()
        outbox = []

        def __init__(self, host, port):
            pass
        
        @classmethod
        def mocked_send(cls, message):
            cls.queue.put(message.serialize())
            sleep(0)
        
        def recv(self):
            results = self.queue.get()
            return Message.unserialize(results)
        
        def send(self, message):
            self.outbox.append(message.serialize())
    
    return MockedRpcServer


class TestMasterRunner(LocustTestCase):
    def setUp(self):
        global_stats.reset_all()
        self._slave_report_event_handlers = [h for h in events.slave_report._handlers]

        parser, _, _ = parse_options()
        args = [
            "--clients", "10",
            "--hatch-rate", "10"
        ]
        opts, _ = parser.parse_args(args)
        self.options = opts
        
    def tearDown(self):
        events.slave_report._handlers = self._slave_report_event_handlers
    
    def test_slave_connect(self):
        import mock
        
        class MyTestLocust(Locust):
            pass
        
        with mock.patch("locust.rpc.rpc.Server", mocked_rpc_server()) as server:
            master = MasterLocustRunner(MyTestLocust, self.options)
            server.mocked_send(Message("client_ready", None, "zeh_fake_client1"))
            self.assertEqual(1, len(master.clients))
            self.assertTrue("zeh_fake_client1" in master.clients, "Could not find fake client in master instance's clients dict")
            server.mocked_send(Message("client_ready", None, "zeh_fake_client2"))
            server.mocked_send(Message("client_ready", None, "zeh_fake_client3"))
            server.mocked_send(Message("client_ready", None, "zeh_fake_client4"))
            self.assertEqual(4, len(master.clients))
            
            server.mocked_send(Message("quit", None, "zeh_fake_client3"))
            self.assertEqual(3, len(master.clients))
    
    def test_slave_stats_report_median(self):
        import mock
        
        class MyTestLocust(Locust):
            pass
        
        with mock.patch("locust.rpc.rpc.Server", mocked_rpc_server()) as server:
            master = MasterLocustRunner(MyTestLocust, self.options)
            server.mocked_send(Message("client_ready", None, "fake_client"))
            
            master.stats.get("/", "GET").log(100, 23455)
            master.stats.get("/", "GET").log(800, 23455)
            master.stats.get("/", "GET").log(700, 23455)
            
            data = {"user_count":1}
            events.report_to_master.fire(client_id="fake_client", data=data)
            master.stats.clear_all()
            
            server.mocked_send(Message("stats", data, "fake_client"))
            s = master.stats.get("/", "GET")
            self.assertEqual(700, s.median_response_time)
    
    def test_master_total_stats(self):
        import mock
        
        class MyTestLocust(Locust):
            pass
        
        with mock.patch("locust.rpc.rpc.Server", mocked_rpc_server()) as server:
            master = MasterLocustRunner(MyTestLocust, self.options)
            server.mocked_send(Message("client_ready", None, "fake_client"))
            stats = RequestStats()
            stats.log_request("GET", "/1", 100, 3546)
            stats.log_request("GET", "/1", 800, 56743)
            stats2 = RequestStats()
            stats2.log_request("GET", "/2", 700, 2201)
            server.mocked_send(Message("stats", {
                "stats":stats.serialize_stats(), 
                "stats_total": stats.total.serialize(),
                "errors":stats.serialize_errors(),
                "user_count": 1,
            }, "fake_client"))
            server.mocked_send(Message("stats", {
                "stats":stats2.serialize_stats(), 
                "stats_total": stats2.total.serialize(),
                "errors":stats2.serialize_errors(),
                "user_count": 2,
            }, "fake_client"))
            self.assertEqual(700, master.stats.total.median_response_time)
    
    def test_master_current_response_times(self):
        import mock
        
        class MyTestLocust(Locust):
            pass
        
        start_time = 1
        with mock.patch("time.time") as mocked_time:
            mocked_time.return_value = start_time
            global_stats.reset_all()
            with mock.patch("locust.rpc.rpc.Server", mocked_rpc_server()) as server:
                master = MasterLocustRunner(MyTestLocust, self.options)
                mocked_time.return_value += 1
                server.mocked_send(Message("client_ready", None, "fake_client"))
                stats = RequestStats()
                stats.log_request("GET", "/1", 100, 3546)
                stats.log_request("GET", "/1", 800, 56743)
                server.mocked_send(Message("stats", {
                    "stats":stats.serialize_stats(),
                    "stats_total": stats.total.get_stripped_report(),
                    "errors":stats.serialize_errors(),
                    "user_count": 1,
                }, "fake_client"))
                mocked_time.return_value += 1
                stats2 = RequestStats()
                stats2.log_request("GET", "/2", 400, 2201)
                server.mocked_send(Message("stats", {
                    "stats":stats2.serialize_stats(),
                    "stats_total": stats2.total.get_stripped_report(),
                    "errors":stats2.serialize_errors(),
                    "user_count": 2,
                }, "fake_client"))
                mocked_time.return_value += 4
                self.assertEqual(400, master.stats.total.get_current_response_time_percentile(0.5))
                self.assertEqual(800, master.stats.total.get_current_response_time_percentile(0.95))
                
                # let 10 second pass, do some more requests, send it to the master and make
                # sure the current response time percentiles only accounts for these new requests
                mocked_time.return_value += 10
                stats.log_request("GET", "/1", 20, 1)
                stats.log_request("GET", "/1", 30, 1)
                stats.log_request("GET", "/1", 3000, 1)
                server.mocked_send(Message("stats", {
                    "stats":stats.serialize_stats(),
                    "stats_total": stats.total.get_stripped_report(),
                    "errors":stats.serialize_errors(),
                    "user_count": 2,
                }, "fake_client"))
                self.assertEqual(30, master.stats.total.get_current_response_time_percentile(0.5))
                self.assertEqual(3000, master.stats.total.get_current_response_time_percentile(0.95))
    
    def test_spawn_zero_locusts(self):
        class MyTaskSet(TaskSet):
            @task
            def my_task(self):
                pass
            
        class MyTestLocust(Locust):
            task_set = MyTaskSet
            min_wait = 100
            max_wait = 100
        
        runner = LocalLocustRunner([MyTestLocust], self.options)
        
        timeout = gevent.Timeout(2.0)
        timeout.start()
        
        try:
            runner.start_hatching(0, 1, wait=True)
            runner.greenlet.join()
        except gevent.Timeout:
            self.fail("Got Timeout exception. A locust seems to have been spawned, even though 0 was specified.")
        finally:
            timeout.cancel()
    
    def test_spawn_uneven_locusts(self):
        """
        Tests that we can accurately spawn a certain number of locusts, even if it's not an 
        even number of the connected slaves
        """
        import mock
        
        class MyTestLocust(Locust):
            pass
        
        with mock.patch("locust.rpc.rpc.Server", mocked_rpc_server()) as server:
            master = MasterLocustRunner(MyTestLocust, self.options)
            for i in range(5):
                server.mocked_send(Message("client_ready", None, "fake_client%i" % i))
            
            master.start_hatching(7, 7)
            self.assertEqual(5, len(server.outbox))
            
            num_clients = 0
            for msg in server.outbox:
                num_clients += sum(six.itervalues(Message.unserialize(msg).data["num_clients"]))
            
            self.assertEqual(7, num_clients, "Total number of locusts that would have been spawned is not 7")

    def test_spawn_per_locust_count(self):
        class MyTestLocust(Locust):
            pass

        class MyTestLocust2(Locust):
            pass

        with mock.patch("locust.rpc.rpc.Server", mocked_rpc_server()) as server:
            master = MasterLocustRunner([MyTestLocust, MyTestLocust2], self.options)
            for i in range(5):
                server.mocked_send(Message("client_ready", None, "fake_client%i" % i))

            locust_count = {MyTestLocust: 11, MyTestLocust2: 20}
            hatch_rate = {MyTestLocust: 1.0, MyTestLocust2: 10.0}
            master.start_hatching(locust_count=locust_count, hatch_rate=hatch_rate)

            self.assertEqual(5, len(server.outbox))

            num_clients = {}
            sent_hatch_rate = {}
            for msg in server.outbox:
                for k, v in six.iteritems(Message.unserialize(msg).data['num_clients']):
                    k = master.locust_classes_by_name[k]
                    num_clients.setdefault(k, 0)
                    num_clients[k] += v

                for k, v in six.iteritems(Message.unserialize(msg).data['hatch_rate']):
                    k = master.locust_classes_by_name[k]
                    sent_hatch_rate.setdefault(k, 0.0)
                    sent_hatch_rate[k] += v

            self.assertEqual(num_clients, locust_count)
            self.assertEqual(set(hatch_rate.keys()), set(sent_hatch_rate.keys()))

            for cls in hatch_rate:
                self.assertAlmostEqual(hatch_rate[k], sent_hatch_rate[k])

    def test_spawn_fewer_locusts_than_slaves(self):
        import mock
        
        class MyTestLocust(Locust):
            pass
        
        with mock.patch("locust.rpc.rpc.Server", mocked_rpc_server()) as server:
            master = MasterLocustRunner(MyTestLocust, self.options)
            for i in range(5):
                server.mocked_send(Message("client_ready", None, "fake_client%i" % i))
            
            master.start_hatching(2, 2)
            self.assertEqual(5, len(server.outbox))
            
            num_clients = 0
            for msg in server.outbox:
                num_clients += sum(six.itervalues(Message.unserialize(msg).data["num_clients"]))
            
            self.assertEqual(2, num_clients, "Total number of locusts that would have been spawned is not 2")
    
    def test_exception_in_task(self):
        class HeyAnException(Exception):
            pass
        
        class MyLocust(Locust):
            class task_set(TaskSet):
                @task
                def will_error(self):
                    raise HeyAnException(":(")
        
        runner = LocalLocustRunner([MyLocust], self.options)
        
        l = MyLocust()
        l._catch_exceptions = False
        
        self.assertRaises(HeyAnException, l.run)
        self.assertRaises(HeyAnException, l.run)
        self.assertEqual(1, len(runner.exceptions))
        
        hash_key, exception = runner.exceptions.popitem()
        self.assertTrue("traceback" in exception)
        self.assertTrue("HeyAnException" in exception["traceback"])
        self.assertEqual(2, exception["count"])
    
    def test_exception_is_catched(self):
        """ Test that exceptions are stored, and execution continues """
        class HeyAnException(Exception):
            pass
        
        class MyTaskSet(TaskSet):
            def __init__(self, *a, **kw):
                super(MyTaskSet, self).__init__(*a, **kw)
                self._task_queue = [
                    {"callable":self.will_error, "args":[], "kwargs":{}}, 
                    {"callable":self.will_stop, "args":[], "kwargs":{}},
                ]
            
            @task(1)
            def will_error(self):
                raise HeyAnException(":(")
            
            @task(1)
            def will_stop(self):
                self.interrupt()
        
        class MyLocust(Locust):
            min_wait = 10
            max_wait = 10
            task_set = MyTaskSet
        
        runner = LocalLocustRunner([MyLocust], self.options)
        l = MyLocust()
        
        # supress stderr
        with mock.patch("sys.stderr") as mocked:
            l.task_set._task_queue = [l.task_set.will_error, l.task_set.will_stop]
            self.assertRaises(LocustError, l.run) # make sure HeyAnException isn't raised
            l.task_set._task_queue = [l.task_set.will_error, l.task_set.will_stop]
            self.assertRaises(LocustError, l.run) # make sure HeyAnException isn't raised
        self.assertEqual(2, len(mocked.method_calls))
        
        # make sure exception was stored
        self.assertEqual(1, len(runner.exceptions))
        hash_key, exception = runner.exceptions.popitem()
        self.assertTrue("traceback" in exception)
        self.assertTrue("HeyAnException" in exception["traceback"])
        self.assertEqual(2, exception["count"])


class TestLocustRunnerCalculations(LocustTestCase):
    class MyTestLocust1(Locust):
        weight = 1
        class task_set(TaskSet):
            @task
            def foo(self): pass

    class MyTestLocust2(Locust):
        weight = 1
        class task_set(TaskSet):
            @task
            def foo(self): pass

    def setUp(self):
        super(TestLocustRunnerCalculations, self).setUp()
        global_stats.reset_all()

        parser, _, _ = parse_options()
        args = [
            "--clients", "10",
            "--hatch-rate", "10"
        ]
        opts, _ = parser.parse_args(args)
        self.options = opts
        self.runner = LocalLocustRunner([self.MyTestLocust1, self.MyTestLocust2], opts)

    def test_locust_classes_by_name(self):
        self.assertEqual(
            self.runner.locust_classes_by_name,
            {'MyTestLocust1': self.MyTestLocust1, 'MyTestLocust2': self.MyTestLocust2})

    def test_num_clients_getter(self):
        self.runner.num_clients_by_class.update({self.MyTestLocust1: 10, self.MyTestLocust2: 20})
        self.assertEqual(self.runner.num_clients, 30)

    def test_num_clients_setter(self):
        self.runner.num_clients = 30
        self.assertEqual(
            self.runner.num_clients_by_class, {self.MyTestLocust1: 15, self.MyTestLocust2: 15})

    def test_num_clients_setter_scaling(self):
        self.runner.num_clients_by_class[self.MyTestLocust1] = 1
        self.runner.num_clients_by_class[self.MyTestLocust2] = 2

        self.runner.num_clients = 30
        self.assertEqual(
            self.runner.num_clients_by_class, {self.MyTestLocust1: 10, self.MyTestLocust2: 20})

    def test_hatch_rate(self):
        self.runner.hatch_rate = 100.5
        self.assertAlmostEqual(self.runner.hatch_rate, 100.5)
        self.assertAlmostEqual(self.runner.hatch_rates[self.MyTestLocust1], 50.25)
        self.assertAlmostEqual(self.runner.hatch_rates[self.MyTestLocust2], 50.25)

    def test_weight_locusts(self):
        buckets = self.runner.weight_locusts(100)
        counted_buckets = {
            cls: len(list(items))
            for cls, items in itertools.groupby(sorted(buckets, key=lambda t: t.__name__))
        }
        self.assertEqual(counted_buckets, {self.MyTestLocust1: 50, self.MyTestLocust2: 50})

    def test_spawn_locust(self):
        slept_time = {}
        spawned_locusts = {}
        def sleep_collector(seconds):
            slept_time.setdefault(seconds, 0)
            slept_time[seconds] += 1

        def spawn_collector(_, locust_class):
            spawned_locusts.setdefault(locust_class, 0)
            spawned_locusts[locust_class] += 1

        locust_count = {self.MyTestLocust1: 10, self.MyTestLocust2: 20}
        hatch_rate = {self.MyTestLocust1: 20.0, self.MyTestLocust2: 30.0}

        with mock.patch("gevent.sleep", side_effect=sleep_collector):
            self.runner.locusts = mock.MagicMock(spawn=spawn_collector)
            self.runner.start_hatching(locust_count=locust_count, hatch_rate=hatch_rate)
            self.runner.hatching_greenlet.join()

        self.assertEqual(len(slept_time), 2)
        self.assertAlmostEqual(sorted(slept_time.keys())[0], 1/30.0)
        self.assertAlmostEqual(sorted(slept_time.keys())[1], 1/20.0)

        self.assertEqual(spawned_locusts, locust_count)

    def test_downscale_locusts(self):
        locust_count = {self.MyTestLocust1: 10, self.MyTestLocust2: 20}
        hatch_rate = {self.MyTestLocust1: 999, self.MyTestLocust2: 999}

        self.runner.start_hatching(locust_count, hatch_rate)
        self.runner.hatching_greenlet.join()

        self.assertEqual(self.runner.user_count, 30)
        self.runner.start_hatching({self.MyTestLocust1: 6, self.MyTestLocust2: 12})
        self.runner.hatching_greenlet.join()
        # self.assertEqual(self.runner.user_count, 18)
        self.assertEqual(self.runner.num_clients_by_class,
                         {self.MyTestLocust1: 6, self.MyTestLocust2: 12})


class TestMessageSerializing(unittest.TestCase):
    def test_message_serialize(self):
        msg = Message("client_ready", None, "my_id")
        rebuilt = Message.unserialize(msg.serialize())
        self.assertEqual(msg.type, rebuilt.type)
        self.assertEqual(msg.data, rebuilt.data)
        self.assertEqual(msg.node_id, rebuilt.node_id)
