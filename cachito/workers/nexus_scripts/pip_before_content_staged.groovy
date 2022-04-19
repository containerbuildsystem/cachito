/*
This script configures Nexus so that Cachito can stage Python content for the Cachito request.

This script creates a PyPI hosted repository and a raw repository to be used by a Cachito request
to fetch Python content

No permissions are configured since it is expected that Cachito's Nexus service account has access
to use all Python related repositories managed by the Nexus instance.
 */
import groovy.json.JsonSlurper
import groovy.transform.Field
import org.slf4j.Logger
import org.slf4j.LoggerFactory
import org.sonatype.nexus.repository.config.Configuration
import org.sonatype.nexus.repository.config.WritePolicy


// Scope logger to the script using @Field
@Field final Logger logger = LoggerFactory.getLogger('cachito');


def createHostedRepo(String name, String repoType) {
    WritePolicy writePolicy = WritePolicy.ALLOW_ONCE
    Boolean strictContentValidation = true
    String blobStoreName = "cachito-pip"
    // repository is an object that is injected by Nexus when the script is executed
    if(repository.repositoryManager.exists(name)) {
        logger.info("Modifying the hosted repository ${name}")
        Configuration hostedRepoConfig = repository.repositoryManager.get(name).configuration
        def storage = hostedRepoConfig.attributes('storage')
        storage.set('strictContentTypeValidation', strictContentValidation)
        storage.set('writePolicy', writePolicy)
        repository.repositoryManager.update(hostedRepoConfig)
    }
    else {
        logger.info("Creating the hosted ${repoType} repository ${name}")
        switch(repoType) {
            case "raw":
                repository.createRawHosted(name, blobStoreName, strictContentValidation, writePolicy)
                break;
            case "pypi":
                repository.createPyPiHosted(name, blobStoreName, strictContentValidation, writePolicy)
                break;
            default:
                logger.warn("Type ${repoType} not supported. repository ${name} not created.")
                break;
        }
    }
}


request = new JsonSlurper().parseText(args)
['pip_repository_name', 'raw_repository_name'].each { param ->
    assert request.get(param): "The ${param} parameter is required"
}

createHostedRepo(request.pip_repository_name, "pypi")
createHostedRepo(request.raw_repository_name, "raw")

return 'The repositories were created successfully'
