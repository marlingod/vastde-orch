# Enabling DataEngine on dc-tenant — end-to-end

## Architecture

```mermaid
flowchart TB
    subgraph OP["Operator host (Mac)"]
        ORCH["vastde-orch enable<br/>(--skip-preflight<br/>--skip-k8s-bootstrap)"]
        VASTDE["vastde CLI<br/>compute-clusters link<br/>container-registries link<br/>setup-dataengine"]
    end

    subgraph VMS["VAST cluster · var203.selab.vastdata.com"]
        TENANT["tenant dc-tenant<br/>data_engine_enabled = True"]
        VIPPOOL["vippool dc-vipool<br/>PROTOCOLS · cidr=24"]
        IDENTITY["group dc-de-users (gid 75500)<br/>user dc-de-owner (uid 75500)<br/>role + manager dc-tenant-admin"]
        BROKER["view /dc-de-broker<br/>protocols: S3 · DATABASE · KAFKA<br/>on dc-vipool"]
        POLICY["s3policy data-engine-dc-tenant<br/>+ /dataengine + /dataengine-telemetries-*"]
        TENANT --> VIPPOOL
        TENANT --> IDENTITY
        TENANT --> BROKER
        TENANT --> POLICY
    end

    subgraph MASTER["k8s master · 10.143.2.247 (Linux)"]
        ZARF["./zarf init --storage-class=local-path<br/>./zarf package deploy dataengine"]
    end

    subgraph K8S["Kubernetes cluster"]
        direction TB
        NSZARF["ns: zarf<br/>injector · seed-registry · registry · agent"]
        NSDE["ns: vast-dataengine<br/>keda · knative-operator<br/>vast-operator-controller-manager<br/>vast-telemetries-collector × 5"]
        NSKE["ns: knative-eventing<br/>(+ kafka source/broker)"]
        NSKS["ns: knative-serving"]
        NSLP["ns: local-path-storage<br/>default StorageClass: local-path"]
    end

    ORCH -- "vastpy / HTTPS REST" --> VMS
    VASTDE -- "vastde HTTPS REST" --> VMS
    OP -- "scp + ssh<br/>(packages/ → /home/vastdata/)" --> MASTER
    ZARF -- "kubectl apply" --> K8S
    VMS -. "HTTPS + mTLS<br/>(cluster reg + telemetry)" .-> K8S

    classDef stageB fill:#fef3c7,stroke:#92400e,color:#000
    STAGEB["Stage B (next): vastde functions / pipelines / triggers"]:::stageB
    K8S --> STAGEB
```

## Integration plan
                                                                                                                                                              
  The pieces complement each other cleanly — vastde-orch handles everything VMS REST supports; vastde fills the two gaps:                                       
                                                                                                                                                                
  ┌─────────────────────────────────────────┬──────────────────────────────┐                                                                                    
  │ vastde-orch enable (vastpy / REST)      │ vastde CLI (DataEngine API)  │                                                                                    
  ├─────────────────────────────────────────┼──────────────────────────────┤                                                                                    
  │ tenant, vippool, identity (group/user), │ compute-clusters link        │                                                                                    
  │ tenant-admin manager+role+perms,        │ container-registries link    │                                                                                    
  │ broker view (S3/DATABASE/KAFKA),        │                              │                                                                                    
  │ view policy, s3policy,                  │ (functions, pipelines,       │                                                                                    
  │ /dataengine/setup-provisioning toggle   │  triggers — for Stage B)     │                                                                                    
  └─────────────────────────────────────────┴──────────────────────────────┘                                                                                    
                                                                                                                                                                
  Concrete steps                                                                                                                                                
                                                                                                                                                                
  1) Decode the .b64 certs to PEM for vastde                                                                                                                    
                                                                                                                                                                
  mkdir -p /Users/yemalin.godonou/Documents/vast/dataengine/sample/kube-creds/pem                                                                             
  cd /Users/yemalin.godonou/Documents/vast/dataengine/sample/kube-creds                                                                                         
  for f in ca client-cert key-client; do base64 -d -i ${f}.b64 -o pem/${f}.pem; done
                                                                                                                                                                
  2) Initialize vastde config (tenant-admin creds)                                                                                                              
                                                                                                                                                                
  set -a && source /Users/yemalin.godonou/Documents/vast/dataengine/.env && set +a                                                                              
  vastde config init \                                                                                                                                          
    --vms-url "https://${VMS_ADDRESS}" \                                                                                                                      
    --tenant "${TENANT_ADMIN_USER}" \                                                                                                                           
    --username "${TENANT_ADMIN_USER}" \                                                                                                                         
    --password "${TENANT_ADMIN_PASSWORD}" 
                                                                                                                                                                
  Stored at ~/.vast/config.toml with 0600 perms. TENANT_ADMIN_USER=dc-tenant happens to match the tenant name, so the same value satisfies both --tenant and    
  --username.                             
                                                                                                                                                                
  3) Run vastde-orch enable (VMS side — tenant, identity, broker, dataengine toggle)                                                                            
                                          
  cd /Users/yemalin.godonou/Documents/vast/dataengine                                                                                                           
  source .venv/bin/activate                                                                                                                                     
  vastde-orch enable -c sample/test-tenant.yaml --skip-preflight --skip-k8s-bootstrap --non-interactive
                                                                                                                                                                
  4) Link the K8s compute cluster                                                                                                                             
                                                                                                                                                                
  vastde compute-clusters link \
    --name dc-k8s-cluster \                                                                                                                                     
    --kube-api-url https://10.143.2.247:6443 \                                                                                                                
    --ca-path        sample/kube-creds/pem/ca.pem \                                                                                                             
    --client-cert-path sample/kube-creds/pem/client-cert.pem \                                                                                                
    --client-key-path  sample/kube-creds/pem/key-client.pem \
    --namespaces vast-dataengine                                                                                                                                
  
  5) Link the container registry                                                                                                                                
                                                                                                                                                              
  vastde container-registries link \                                                                                                                            
    --name dc-dockerhub \                                                                                                                                     
    --url docker.io \                     
    --primary-cluster dc-k8s-cluster \
    --primary-namespace vast-dataengine \                                                                                                                       
    --auth-type password \                    
    --username "${REGISTRY_USER}" \                                                                                                                             
    --password "${REGISTRY_PASSWORD}" 