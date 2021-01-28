from kubernetes import client, watch, config
import time
import hashlib
from kubernetes.stream import stream
import os
import json
import traceback
from logs import *


account_dir = '/var/run/secrets/kubernetes.io/serviceaccount'
api_server = os.getenv("api_server", "https://kubernetes.default.svc.cluster.local")
forbidden_namespaces = os.getenv("forbidden_namespaces", "kube-system ingress-nginx").split()

try:
    config.load_kube_config()                           # load local kubeconfig
    v1 = client.CoreV1Api()
except config.config_exception.ConfigException:
    with open(f'{account_dir}/token', 'r') as f:        # use mounted serviceaccount token
        token = f.__next__().strip()
    configuration = client.Configuration()
    configuration.ssl_ca_cert = f'{account_dir}/ca.crt'
    configuration.api_key['authorization'] = token
    configuration.api_key_prefix['authorization'] = 'Bearer'
    configuration.host = api_server
    v1 = client.CoreV1Api(client.ApiClient(configuration))


namespace = 'default'


def exec_command_in_pod(pod, container, command,  api_instance=v1, namespace=namespace, shell="/bin/sh"):
    print(command)
    client = stream(api_instance.connect_get_namespaced_pod_exec,
                  pod.metadata.name,
                  namespace,
                  command=[shell, '-c', command],
                  container=container,
                  stderr=True, stdin=False,
                  stdout=True, tty=False, _preload_content=False)
    client.run_forever(timeout=60)
    return [client.read_channel(i) for i in range(3)] + [json.loads(client.read_channel(3))]
    # stdin, stdout, stderr, error_channel
    # https://github.com/kubernetes-client/python-base/blob/master/stream/ws_client.py



def find_pods_with_configmap(cm, api_instance=v1, namespace=namespace):
    res = []
    for p in [pod for pod in api_instance.list_namespaced_pod(namespace).items if pod.metadata.annotations and
              'reloader.yarr/command' in pod.metadata.annotations]:
        for v in p.spec.volumes:
            try:
                if hasattr(v, 'config_map') and v.config_map.name == cm.metadata.name:
                    for c in p.spec.containers:
                        for vm in c.volume_mounts:
                            if vm.name == v.name:
                                if vm.sub_path is None and vm.sub_path_expr is None:
                                    res.append((p, c.name, vm.mount_path))
                                else:
                                    logger.info(f"Configmap {c.name} can't be reloaded in pod {p.metadata.name}:"
                                                f"mounted as subPath and won't be updated")
            except AttributeError as err:
                pass
    return res  # pod, container name, mountpath


def comparemd5(cm, pod, container, mountpath, namespace):
    cm_hashes = {file: hashlib.md5(cm.data[file].encode()).hexdigest() for file in cm.data}
    pod_hashes = {file: exec_command_in_pod(pod, container,
                                            f'md5sum {mountpath}{file}', namespace=namespace)[1].split()[0] for file in
                  cm_hashes}
    logger.info(cm_hashes, pod_hashes)
    return cm_hashes == pod_hashes


def touch_annotation(pod, api_instance=v1, namespace=namespace):
    current_milli_time = lambda: int(round(time.time() * 1000))
    pod.metadata.annotations['reloader.yarr/updated'] = str(current_milli_time())
    api_instance.patch_namespaced_pod(name=pod.metadata.name, namespace=namespace, body=pod)
    logger.info(f'touched annotation on {pod.metadata.name}')


def wait_and_reload(cm, pod, container, mountpath, namespace):
    while not comparemd5(cm, pod, container, mountpath, namespace):
        logger.info(pod.metadata.name, 'waiting')
        time.sleep(2)

    if 'reloader.yarr/check' in pod.metadata.annotations:
        check_result = exec_command_in_pod(pod, container, pod.metadata.annotations['reloader.yarr/check'],
                                           namespace=namespace)
        if check_result[3]["status"] == 'Success':
            logger.info(f'Config check in {pod.metadata.name} succeeded')
        else:
            logger.error(f'Config check in {pod.metadata.name} failed:\n {check_result[3]["message"]}\n '
                  f"stderr: {check_result[2]}")
            return None

    command = pod.metadata.annotations['reloader.yarr/command']
    reload_result = exec_command_in_pod(pod, container, command, namespace=namespace)

    if reload_result[3]["status"] == 'Success':
        logger.info(f'Reload in {pod.metadata.name} succeeded')
    else:
        logger.error(f'Reload check in {pod.metadata.name} failed:\n {reload_result[3]["message"]}\n '
              f"stderr: {reload_result[2]}")


w = watch.Watch()

for event in w.stream(v1.list_config_map_for_all_namespaces):
    try:
        namespace = event['object'].metadata.namespace
        if event['type'] == 'MODIFIED' and namespace not in forbidden_namespaces:
            logger.info(f"Event: {event['type']} {event['object'].metadata.name} in namespace {namespace}")
            pods_to_reload = find_pods_with_configmap(cm=event['object'], namespace=namespace)
            if pods_to_reload:
                for i in pods_to_reload:
                    p, c, mp = i
                    touch_annotation(p, namespace=namespace)
                    wait_and_reload(event['object'], p, c, mp, namespace=namespace)
    except Exception:
        logger.exception(traceback.format_exc())
