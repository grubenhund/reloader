# reloader

Watches configmap changes in cluster and performs reload on containers using that configmaps with restarting.

Controlled by pod annotations:

| Annotation | Effect |
| ------ | ------ |
| reloader.yarr/command | Command to reload. If unset, reloader doesn't interact with pod |
| reloader.yarr/check | Command to check config before reload, not necessary |

reloader.yarr/updated is set before reloading with purpose of triggering k8s control loop to update configmap inside pod.

For local usage run with env `api_server` and kubeconfig in ~/.kube/config